"""Real dashboard endpoint/ORM regression, valid on SQLite and PostgreSQL.

SQLite exercises the shared recent-activity filter. PostgreSQL additionally
exercises the JSON has-key prefilter; a SQLite pass does not prove that branch.
"""
from datetime import timedelta
from django.utils import timezone
from application.models.carnet_models import Carnet, CarnetSubscription
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_sport_association, create_test_subscription


class DashboardExpiringCarnetTests(BaseAPITestCase):
    def assignment(self, association, label, remaining, activity_days=None, include_registry=True):
        registration=create_test_subscription(sport_association=association)
        definition=Carnet.objects.create(user_id=association.user, sport_association=association,
            title=label, lessons_number=5, fee=50)
        meta={'lessons_left':remaining,'lessons_counter':5}
        if include_registry:
            meta['lessons_registry']=[] if activity_days is None else [{
                'date':(timezone.now()+timedelta(days=activity_days)).strftime('%Y-%m-%d %H:%M:%S%z'),
                'title':'Actual prior lesson'}]
        return CarnetSubscription.objects.create(user_id=association.user,subscription=registration,
            carnet_id=definition,meta=meta)

    def read(self):
        response=self.client.get('/statistic/dashboard',{'widget':'expiringcarnets'})
        self.assertEqual(response.status_code,200,response.content)
        return response.data['data']['expiring_carnets']

    def test_recent_zero_and_one_three_are_shown_stale_zero_and_four_are_excluded(self):
        recent=self.assignment(self.sport_association,'Recent zero',0,-1)
        self.assignment(self.sport_association,'Stale zero',0,-31)
        self.assignment(self.sport_association,'Zero without usage',0)
        self.assignment(self.sport_association,'Zero without registry key',0,include_registry=False)
        one=self.assignment(self.sport_association,'One left',1)
        three=self.assignment(self.sport_association,'Three left',3)
        self.assignment(self.sport_association,'Four left',4)
        rows=self.read()
        self.assertEqual([str(row['carnet_subscription_id']) for row in rows],list(map(str,[recent.pk,one.pk,three.pk])))
        self.assertEqual([row['carnet']['meta']['lessons_left'] for row in rows],[0,1,3])

    def test_other_associations_assignments_are_never_returned(self):
        own=self.assignment(self.sport_association,'Own carnet',1)
        foreign_association=create_test_sport_association()
        foreign=self.assignment(foreign_association,'Foreign carnet',1)
        self.assertEqual([str(row['carnet_subscription_id']) for row in self.read()],[str(own.pk)])
        self.client.force_authenticate(user=foreign_association.user)
        self.assertEqual([str(row['carnet_subscription_id']) for row in self.read()],[str(foreign.pk)])
