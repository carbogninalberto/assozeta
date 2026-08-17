import asyncio
import json
import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache

from application.chat.throttling import WebSocketThrottle, ConcurrencyGuard
from application.mcp_server.server import tool_save_report, tool_list_reports

logger = logging.getLogger(__name__)

SAVE_INFO_CACHE_TTL = 3600  # 1 hour


class WebSocketAgentCallback:
    """Bridges AgentCallback protocol to WebSocket send_json calls."""

    def __init__(self, consumer: 'AgentConsumer'):
        self.consumer = consumer

    async def on_status(self, status: str) -> None:
        await self.consumer.send_json({
            'type': 'status',
            'status': status,
        })

    async def on_tool_call(self, tool_name: str, arguments: dict) -> None:
        await self.consumer.send_json({
            'type': 'tool_call',
            'tool': tool_name,
            'arguments': arguments,
        })

    async def on_message(self, message: str) -> None:
        await self.consumer.send_json({
            'type': 'message',
            'content': message,
        })

    async def on_message_chunk(self, chunk: str) -> None:
        await self.consumer.send_json({
            'type': 'message_chunk',
            'content': chunk,
        })

    async def on_message_end(self) -> None:
        await self.consumer.send_json({
            'type': 'message_end',
        })

    async def on_export_ready(self, export_data: dict) -> None:
        if 'save_info' in export_data:
            self.consumer.last_save_info = export_data['save_info']
            self.consumer.last_export_filename = export_data.get('filename', 'export')
            # Persist to cache so it survives WebSocket reconnections
            user_id = str(self.consumer.user.user_id) if self.consumer.user else None
            if user_id:
                await database_sync_to_async(cache.set)(
                    f'agent_save_info:{user_id}',
                    {'save_info': export_data['save_info'], 'filename': export_data.get('filename', 'export')},
                    SAVE_INFO_CACHE_TTL,
                )

        payload = {
            'type': export_data.get('type', 'export_ready'),
            'filename': export_data.get('filename', 'export'),
            'content_type': export_data.get('content_type', ''),
            'data_base64': export_data.get('data_base64', ''),
            'row_count': export_data.get('row_count', 0),
            'can_save': 'save_info' in export_data,
        }
        if 'total_count' in export_data:
            payload['total_count'] = export_data['total_count']
        if 'total_dates' in export_data:
            payload['total_dates'] = export_data['total_dates']
        if 'sheet_count' in export_data:
            payload['sheet_count'] = export_data['sheet_count']
        if 'save_info' in export_data:
            payload['default_name'] = export_data['save_info'].get('name', '')
            payload['default_description'] = export_data['save_info'].get('description', '')
            payload['description_hint'] = export_data['save_info'].get('description_hint', '')
        await self.consumer.send_json(payload)

    async def on_report_saved(self, report_data: dict) -> None:
        await self.consumer.send_json({
            'type': 'report_saved',
            'saved_report_id': report_data.get('saved_report_id', ''),
            'name': report_data.get('name', ''),
        })

    async def on_error(self, error: str) -> None:
        await self.consumer.send_json({
            'type': 'error',
            'message': error,
        })

    async def on_done(self) -> None:
        await self.consumer.send_json({
            'type': 'done',
        })


class AgentConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket consumer for the AI agent chat."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.sport_association = None
        self.agent = None
        self.agent_task = None
        self.throttle = None
        self.concurrency_guard = None
        self.last_save_info = None
        self.last_export_filename = None

    async def connect(self):
        """Handle WebSocket connection."""
        self.user = self.scope.get('user')

        # Auth check
        if isinstance(self.user, AnonymousUser) or not self.user:
            logger.warning("Agent WS rejected: not authenticated")
            await self.close(code=4001)
            return

        user_id = str(self.user.user_id)

        # Concurrency check
        self.concurrency_guard = ConcurrencyGuard(user_id)
        if not self.concurrency_guard.acquire():
            logger.warning(f"Agent WS rejected: concurrent session for user {user_id}")
            await self.close(code=4002)
            return

        # Resolve sport_association
        try:
            self.sport_association = await database_sync_to_async(
                lambda: self.user.sport_association
            )()
        except Exception as e:
            logger.warning(f"Agent WS rejected: no sport_association for user {user_id}: {e}")
            self.concurrency_guard.release()
            await self.close(code=4003)
            return

        # Set up throttle
        rate_limit = getattr(settings, 'MCP_AGENT_WS_RATE_LIMIT', 10)
        self.throttle = WebSocketThrottle(user_id, max_messages=rate_limit)

        # Initialize agent
        await self._init_agent()

        await self.accept()
        logger.info(f"Agent WS connected: user={user_id} association={self.sport_association.denomination}")

        # Send welcome message (no LLM call needed)
        await self.send_json({
            'type': 'message',
            'content': (
                "Ciao! Sono il tuo agente AI.\n\n"
                "Posso aiutarti a:\n"
                "- Cercare informazioni su **tesserati, iscrizioni, corsi, pagamenti**\n"
                "- Contare e visualizzare dati (es. \"Quanti iscritti ho quest'anno?\")\n"
                "- Preparare **export in Excel, CSV o PDF**\n\n"
                "Chiedimi quello che ti serve!"
            ),
        })

    async def _init_agent(self):
        """Initialize the AI agent."""
        from application.agent.core import Agent
        from application.agent.providers.ai_provider import AIProvider

        provider = AIProvider()
        callback = WebSocketAgentCallback(self)

        self.agent = Agent(
            sport_association_id=str(self.sport_association.sport_association_id),
            sport_association_name=self.sport_association.denomination,
            provider=provider,
            callback=callback,
            user_id=str(self.user.user_id),
        )

    async def disconnect(self, close_code):
        """Handle WebSocket disconnect."""
        # Cancel running agent task
        if self.agent_task and not self.agent_task.done():
            self.agent_task.cancel()

        # Release concurrency slot
        if self.concurrency_guard:
            self.concurrency_guard.release()

        logger.info(f"Agent WS disconnected: code={close_code}")

    async def receive_json(self, content):
        """Handle incoming WebSocket messages."""
        msg_type = content.get('type')

        if msg_type == 'ping':
            await self.send_json({
                'type': 'pong',
                'timestamp': content.get('timestamp'),
            })
        elif msg_type == 'user_message':
            await self._handle_user_message(content)
        elif msg_type == 'cancel':
            await self._handle_cancel()
        elif msg_type == 'clear_history':
            await self._handle_clear_history()
        elif msg_type == 'save_report':
            await self._handle_save_report(content)
        elif msg_type == 'delete_report':
            await self._handle_delete_report(content)
        elif msg_type == 'list_reports':
            await self._handle_list_reports()
        else:
            await self.send_json({
                'type': 'error',
                'message': f'Tipo di messaggio sconosciuto: {msg_type}',
            })

    async def _handle_user_message(self, content):
        """Handle a user chat message."""
        message = content.get('message', '').strip()
        if not message:
            return

        # Rate limit check
        if not self.throttle.is_allowed():
            await self.send_json({
                'type': 'error',
                'message': 'Troppi messaggi. Attendi un momento prima di riprovare.',
            })
            return

        # Don't allow concurrent agent tasks
        if self.agent_task and not self.agent_task.done():
            await self.send_json({
                'type': 'error',
                'message': 'Sto ancora elaborando la richiesta precedente. Attendi o annulla.',
            })
            return

        # Run agent in a task with timeout
        timeout = getattr(settings, 'MCP_AGENT_WS_TIMEOUT', 60)
        self.agent_task = asyncio.create_task(
            self._run_agent_with_timeout(message, timeout)
        )

    async def _safe_send(self, data: dict):
        """Send JSON, ignoring errors if the socket is already closed."""
        try:
            await self.send_json(data)
        except Exception:
            pass

    async def _run_agent_with_timeout(self, message: str, timeout: int):
        """Run the agent with a timeout."""
        try:
            await asyncio.wait_for(
                self.agent.process_message(message),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await self._safe_send({
                'type': 'error',
                'message': 'La richiesta ha impiegato troppo tempo. Prova con una richiesta più specifica.',
            })
            await self._safe_send({'type': 'done'})
        except asyncio.CancelledError:
            await self._safe_send({
                'type': 'status',
                'status': 'cancelled',
            })
            await self._safe_send({'type': 'done'})
        except Exception:
            logger.exception("Agent processing error")
            await self._safe_send({
                'type': 'error',
                'message': 'Si è verificato un errore interno. Riprova.',
            })
            await self._safe_send({'type': 'done'})

    async def _handle_cancel(self):
        """Cancel the running agent task."""
        if self.agent_task and not self.agent_task.done():
            self.agent_task.cancel()
            await self.send_json({
                'type': 'status',
                'status': 'cancelling',
            })
        else:
            await self.send_json({
                'type': 'status',
                'status': 'idle',
            })

    async def _handle_clear_history(self):
        """Clear the agent's conversation history."""
        if self.agent:
            self.agent.clear_history()
        await self.send_json({
            'type': 'status',
            'status': 'history_cleared',
        })

    async def _handle_save_report(self, content: dict):
        """Save the last exported report with a custom name."""
        name = content.get('name', '').strip()

        if not name:
            await self.send_json({
                'type': 'error',
                'message': 'Il nome del report è obbligatorio.',
            })
            return

        user_id = str(self.user.user_id) if self.user and hasattr(self.user, 'user_id') else None

        # Try in-memory first, fall back to cache (survives WS reconnections)
        save_info = self.last_save_info
        export_filename = self.last_export_filename
        if not save_info and user_id:
            cached = await database_sync_to_async(cache.get)(f'agent_save_info:{user_id}')
            if cached:
                save_info = cached['save_info']
                export_filename = cached.get('filename', 'export')
                logger.info(f"Restored save_info from cache for user {user_id}")

        logger.info(f"save_report requested: name={name}, save_info={save_info is not None}")

        if not save_info:
            await self.send_json({
                'type': 'error',
                'message': 'Nessun export disponibile da salvare. Genera prima un export.',
            })
            return

        sport_association_id = self.sport_association.sport_association_id if self.sport_association else None

        if not sport_association_id or not user_id:
            await self.send_json({
                'type': 'error',
                'message': 'Informazioni di sessione mancanti. Ricarica la pagina.',
            })
            return

        description = (
            content.get('description', '').strip()
            or save_info.get('description')
            or f"{export_filename} ({save_info.get('description_hint', '')})"
        )

        try:
            result = await database_sync_to_async(tool_save_report)(
                sport_association_id=str(sport_association_id),
                name=name,
                tool_name=save_info.get('tool_name'),
                params=save_info.get('params', {}),
                description=description,
                ui_config=save_info.get('ui_config'),
                user_id=user_id,
            )

            if 'error' in result:
                await self.send_json({
                    'type': 'error',
                    'message': result.get('error', 'Errore nel salvataggio.'),
                })
                return

            await self.send_json({
                'type': 'report_saved',
                'saved_report_id': result.get('saved_report_id', ''),
                'name': result.get('name', name),
                'message': result.get('message', 'Report salvato.'),
            })
        except Exception:
            logger.exception("Error saving report")
            await self.send_json({
                'type': 'error',
                'message': 'Errore nel salvataggio del report. Riprova.',
            })

    async def _handle_delete_report(self, content: dict):
        """Delete a saved report from the frontend."""
        from django.apps import apps
        SavedReport = apps.get_model('application', 'SavedReport')

        saved_report_id = content.get('saved_report_id', '').strip()
        if not saved_report_id:
            await self.send_json({
                'type': 'error',
                'message': 'ID report mancante.',
            })
            return

        sport_association_id = self.sport_association.sport_association_id if self.sport_association else None
        user_id = str(self.user.user_id) if self.user and hasattr(self.user, 'user_id') else None

        if not sport_association_id or not user_id:
            await self.send_json({
                'type': 'error',
                'message': 'Informazioni di sessione mancanti. Ricarica la pagina.',
            })
            return

        try:
            deleted, _ = await database_sync_to_async(
                lambda: SavedReport.objects.filter(
                    saved_report_id=saved_report_id,
                    sport_association_id=sport_association_id,
                    created_by_id=user_id,
                ).delete()
            )()

            if deleted == 0:
                await self.send_json({
                    'type': 'error',
                    'message': 'Report non trovato.',
                })
                return

            await self.send_json({
                'type': 'report_deleted',
                'saved_report_id': saved_report_id,
            })
        except Exception:
            logger.exception("Error deleting report")
            await self.send_json({
                'type': 'error',
                'message': 'Errore nella cancellazione del report. Riprova.',
            })

    async def _handle_list_reports(self):
        """List saved reports for the current user."""
        sport_association_id = self.sport_association.sport_association_id if self.sport_association else None
        user_id = str(self.user.user_id) if self.user and hasattr(self.user, 'user_id') else None

        if not sport_association_id or not user_id:
            await self.send_json({
                'type': 'error',
                'message': 'Informazioni di sessione mancanti. Ricarica la pagina.',
            })
            return

        try:
            result = await database_sync_to_async(tool_list_reports)(
                sport_association_id=sport_association_id,
                user_id=user_id,
            )

            if 'error' in result:
                await self.send_json({
                    'type': 'error',
                    'message': result.get('error', 'Errore nel recupero dei report.'),
                })
                return

            await self.send_json({
                'type': 'report_list',
                'reports': result.get('reports', []),
                'count': result.get('count', 0),
            })
        except Exception:
            logger.exception("Error listing reports")
            await self.send_json({
                'type': 'error',
                'message': 'Errore nel recupero dei report. Riprova.',
            })
