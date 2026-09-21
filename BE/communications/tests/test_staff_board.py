from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_user, create_test_sport_association
from application.models import User
from communications.models import StaffBoardMessage


class StaffBoardTests(BaseAPITestCase):
    endpoint = '/communications/staff-board/'

    def collaborator(self, *actions):
        user = create_test_user(role=User.COLLABORATOR, connected_user=self.user,
            collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=['association.communication.messages.' + action for action in actions])
        self.client.force_authenticate(user=user)
        return user

    def test_text_roundtrip_and_author(self):
        author = self.collaborator('create', 'read', 'update')
        content = 'A & B <script>alert(1)</script>\nNext line'
        response = self.client.post(self.endpoint+'add', {'content': content}, format='json')
        self.assertEqual(response.status_code, 201)
        message = response.json()['data']
        self.assertEqual(message['content'], content)  # Svelte renders text, never raw HTML.
        self.assertEqual(message['author'], str(author.pk))
        response = self.client.patch(self.endpoint+message['staff_board_message_id']+'/update',
            {'content': message['content'], 'pinned': 'false'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['data']['pinned'])
        self.assertEqual(response.json()['data']['content'], content)

    def test_permission_matrix(self):
        message = StaffBoardMessage.objects.create(sport_association=self.sport_association, content='keep')
        self.collaborator('read')
        self.assertEqual(self.client.get(self.endpoint+'list').status_code, 200)
        self.assertEqual(self.client.post(self.endpoint+'add', {'content':'denied'}).status_code, 403)
        self.assertEqual(self.client.patch(self.endpoint+str(message.pk)+'/update', {'pinned':True}).status_code, 403)
        self.assertEqual(self.client.delete(self.endpoint+str(message.pk)+'/delete').status_code, 403)
        message.refresh_from_db()
        self.assertFalse(message.pinned)
        self.collaborator('create')
        self.assertEqual(self.client.get(self.endpoint+'list').status_code, 403)

    def test_association_isolation(self):
        other = create_test_sport_association()
        message = StaffBoardMessage.objects.create(sport_association=other, content='private')
        self.assertEqual(self.client.get(self.endpoint+'list').json()['data'], [])
        for method, suffix in ((self.client.patch,'update'), (self.client.delete,'delete')):
            self.assertEqual(method(self.endpoint+str(message.pk)+'/'+suffix, {'content':'changed'}).status_code, 404)

    def test_invalid_input_is_rejected(self):
        for data in ({'content': None}, {'content': {}}, {'content': 42}, {'content':'  '},
                     {'content':'x'*10001}, {'content':'ok','pinned':'nonsense'}):
            self.assertEqual(self.client.post(self.endpoint+'add', data, format='json').status_code, 400)
        self.assertEqual(StaffBoardMessage.objects.count(), 0)

    def test_athlete_and_anonymous_rejected(self):
        self.client.force_authenticate(user=create_test_user(role=User.ATHLETE))
        self.assertEqual(self.client.get(self.endpoint+'list').status_code, 403)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(self.endpoint+'list').status_code, 401)

    def test_pins_order_and_deleted_author(self):
        author = create_test_user()
        first = StaffBoardMessage.objects.create(sport_association=self.sport_association, content='pinned', pinned=True, author=author)
        StaffBoardMessage.objects.create(sport_association=self.sport_association, content='newer')
        author.delete()
        data = self.client.get(self.endpoint+'list').json()['data']
        self.assertEqual(data[0]['staff_board_message_id'], str(first.pk))
        self.assertEqual(data[0]['author_name'], 'Utente eliminato')

    def test_only_original_author_can_edit_or_delete_even_for_association_owner(self):
        author = self.collaborator('create', 'read', 'update', 'delete')
        author.avatar_image = 'https://example.test/avatar.png'
        author.save(update_fields=['avatar_image'])
        response = self.client.post(self.endpoint+'add', {'content': 'mine'}, format='json')
        message = response.json()['data']
        self.assertTrue(message['is_owner'])
        self.assertEqual(message['author_avatar'], author.avatar_image)
        identifier = message['staff_board_message_id']
        other = self.collaborator('read', 'update', 'delete')
        for user in (other, self.user):
            self.client.force_authenticate(user=user)
            self.assertFalse(self.client.get(self.endpoint+'list').json()['data'][0]['is_owner'])
            self.assertEqual(self.client.patch(self.endpoint+identifier+'/update', {'content': 'stolen'}, format='json').status_code, 403)
            self.assertEqual(self.client.delete(self.endpoint+identifier+'/delete').status_code, 403)
        self.client.force_authenticate(user=author)
        self.assertEqual(self.client.patch(self.endpoint+identifier+'/update', {'content': 'edited'}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(self.endpoint+identifier+'/delete').status_code, 200)

    def test_pin_permission_is_independent_of_message_authorship(self):
        message = StaffBoardMessage.objects.create(sport_association=self.sport_association, author=self.user, content='pin me')
        self.collaborator('update')
        endpoint = self.endpoint+str(message.pk)+'/update'
        self.assertEqual(self.client.patch(endpoint, {'pinned': True}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(endpoint, {'pinned': False, 'content': 'stolen'}, format='json').status_code, 403)
        message.refresh_from_db()
        self.assertTrue(message.pinned)
        self.assertEqual(message.content, 'pin me')

    def test_rich_document_and_image_roundtrip(self):
        self.collaborator('create', 'read', 'update')
        image = 'data:image/png;base64,iVBORw0KGgo='
        document = {'type': 'doc', 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': 'A < B', 'marks': [{'type': 'bold'}]}]},
            {'type': 'paragraph', 'content': [{'type': 'image', 'attrs': {'src': image, 'alt': 'Foto', 'onerror': 'alert(1)'}}]},
        ]}
        response = self.client.post(self.endpoint+'add', {'content': 'A < B', 'document': document}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        data = response.json()['data']
        self.assertEqual(data['content'], 'A < B')
        self.assertEqual(data['document']['content'][0]['content'][0]['marks'], [{'type': 'bold'}])
        self.assertNotIn('onerror', data['document']['content'][1]['content'][0]['attrs'])
        self.assertEqual(data['document'], self.client.get(self.endpoint+'list').json()['data'][0]['document'])
        image_only = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'image', 'attrs': {'src': image}}]}]}
        response = self.client.patch(self.endpoint+data['staff_board_message_id']+'/update', {'content': 'Immagine allegata', 'document': image_only}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['content'], '')
        response = self.client.post(self.endpoint+'add', {'content': 'Immagine allegata', 'document': image_only}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['data']['document']['content'][0]['content'][0]['attrs']['src'], image)

    def test_rich_document_rejects_empty_and_executable_content(self):
        invalid = [None, {}, {'type': 'doc'}, {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': '   '}]}]},
            {'type': 'doc', 'content': [{'type': 'script', 'text': 'alert(1)'}]},
            {'type': 'doc', 'content': [{'type': 'image', 'attrs': {'src': 'data:image/svg+xml;base64,AAAA'}}]},
            {'type': 'doc', 'content': [{'type': 'image', 'attrs': {'src': 'javascript:alert(1)'}}]},
            {'type': 'doc', 'content': [{'type': 'text', 'text': 'link', 'marks': [{'type': 'link', 'attrs': {'href': 'javascript:alert(1)'}}]}]},
            {'type': 'doc', 'content': [{'type': 'text', 'text': 'link', 'marks': [{'type': 'link', 'attrs': 'bad'}]}]},
        ]
        for document in invalid:
            with self.subTest(document=document):
                self.assertEqual(self.client.post(self.endpoint+'add', {'document': document}, format='json').status_code, 400)
        self.assertFalse(StaffBoardMessage.objects.exists())

    def test_realtime_changes_publish_only_after_commit(self):
        from unittest.mock import AsyncMock, patch
        from communications.staff_board import staff_board_group
        layer = AsyncMock()
        with patch('communications.staff_board.get_channel_layer', return_value=layer):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.endpoint+'add', {'content': 'hello'}, format='json')
                layer.group_send.assert_not_called()
            layer.group_send.assert_awaited_once_with(staff_board_group(self.sport_association.pk), {'type': 'staff_board_changed'})
            identifier = response.json()['data']['staff_board_message_id']
            for method, suffix, body in ((self.client.patch, 'update', {'content': 'updated'}), (self.client.patch, 'update', {'pinned': True}), (self.client.delete, 'delete', {})):
                layer.reset_mock()
                with self.captureOnCommitCallbacks(execute=True):
                    response = method(self.endpoint+identifier+'/'+suffix, body, format='json')
                self.assertEqual(response.status_code, 200)
                layer.group_send.assert_awaited_once()

    def test_realtime_membership_requires_current_tenant_and_read_permission(self):
        from communications.staff_board import readable_association_id
        self.assertEqual(readable_association_id(self.user.pk), str(self.sport_association.pk))
        reader = self.collaborator('read')
        self.assertEqual(readable_association_id(reader.pk), str(self.sport_association.pk))
        reader.collaborator_permissions = []
        reader.save(update_fields=['collaborator_permissions'])
        self.assertIsNone(readable_association_id(reader.pk))
        athlete = create_test_user(role=User.ATHLETE)
        self.assertIsNone(readable_association_id(athlete.pk))



from application.tests.base import BaseTransactionTestCase
from application.tests.fixtures.factories import create_test_billing_subscription


class StaffBoardSocketTests(BaseTransactionTestCase):
    def setUp(self):
        self.user = create_test_user(role=User.ASSOCIATION)
        self.sport_association = create_test_sport_association(user=self.user)
        create_test_billing_subscription(user=self.user, plan_type='pro')

    def test_socket_joins_tenant_group_and_rechecks_permission_before_delivering(self):
        from asgiref.sync import async_to_sync
        from unittest.mock import AsyncMock
        from notifications.consumers import NotificationConsumer
        from communications.staff_board import staff_board_group
        reader = create_test_user(role=User.COLLABORATOR, connected_user=self.user, collaborator_role=User.CUSTOM_COLLABORATOR_ROLE, collaborator_permissions=['association.communication.messages.read'])
        consumer = NotificationConsumer()
        consumer.scope = {'user': reader}
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = 'test-channel'
        consumer.accept = AsyncMock()
        consumer.send_json = AsyncMock()
        consumer._get_user_broadcasts = AsyncMock(return_value=[])
        async_to_sync(consumer.connect)()
        group = staff_board_group(self.sport_association.pk)
        consumer.channel_layer.group_add.assert_any_await(group, 'test-channel')
        async_to_sync(consumer.staff_board_changed)({'type': 'staff_board_changed'})
        consumer.send_json.assert_awaited_once_with({'type': 'staff_board_changed'})
        reader.collaborator_permissions = []
        reader.save(update_fields=['collaborator_permissions'])
        consumer.send_json.reset_mock()
        async_to_sync(consumer.staff_board_changed)({'type': 'staff_board_changed'})
        consumer.send_json.assert_not_called()
        async_to_sync(consumer.disconnect)(1000)
        consumer.channel_layer.group_discard.assert_any_await(group, 'test-channel')
