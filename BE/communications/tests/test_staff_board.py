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
