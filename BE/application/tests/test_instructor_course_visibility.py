from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_course, create_test_user
from application.models import Instructor, User


class InstructorCourseVisibilityTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.staff = create_test_user(role=User.COLLABORATOR, connected_user=self.user,
            collaborator_role=User.CUSTOM_COLLABORATOR_ROLE,
            collaborator_permissions=['association.courses.read'])
        Instructor.objects.create(user=self.user, associated_user_id=self.staff.pk,
                                  first_name='Instructor', last_name='Test')
        self.own = create_test_course(sport_association=self.sport_association, title='Own course')
        create_test_course(title='Other association')
        self.client.force_authenticate(user=self.staff)

    def test_courses_without_attendance_are_visible_but_other_associations_are_not(self):
        for method in (self.client.get, self.client.post):
            response = method('/course/list?all=1')
            self.assertEqual(response.status_code, 200)
            self.assertEqual([c['course_id'] for c in response.json()['data']], [str(self.own.pk)])

    def test_read_permission_is_required(self):
        self.staff.collaborator_permissions = []
        self.staff.save()
        self.assertEqual(self.client.get('/course/list?all=1').status_code, 403)

    def test_search_still_applies(self):
        response = self.client.get('/course/list', {'all':1, 'query[generalSearch]':'missing'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data'], [])
