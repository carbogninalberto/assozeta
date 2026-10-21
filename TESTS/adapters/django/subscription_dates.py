"""Calendar rules and actual Subscription.save date defaults; not full write parity."""
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
import io
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.apps import apps
    from django.conf import settings
    from django.contrib.contenttypes.models import ContentType
    from application import tasks
    from application.models import User, SportAssociation, Subscription
    from application.models.audit_models import AuditLogIndex
    from application.utils.api_utils import BalanceSheetData

    assert settings.TIME_ZONE == 'UTC'
    output=ROOT/'quality-reports/go-migration/subscription-dates'
    output.mkdir(parents=True,exist_ok=True)
    (output/'body-results.json').write_text('{"success":false}\n')
    ranges=[]
    def add(name,created,day,month,start='',end='',message='',custom=False,endday=0,endmonth=0,kind='aware'):
        ranges.append(dict(Name=name,Created=created,Rule=dict(StartDay=day,StartMonth=month,CustomEnd=custom,EndDay=endday,EndMonth=endmonth),
                           Start=start,End=end,Message=message,Kind=kind))
    for year in (2,1900,2000,2023,2024,9998):
        for month in range(1,13):
            for day in (1,15,28):
                start_year=year-int((month,day)>(6,15))
                first=date(start_year,month,day)
                last=date(start_year+1,month,day)-timedelta(days=1)
                add(f'grid-{year}-{month}-{day}',f'{year:04d}-06-15T12:00:00+00:00',day,month,first.isoformat(),last.isoformat())
    for month in range(1,13):
        boundary=datetime(2024,month,1,tzinfo=timezone.utc)
        for offset in (-1,0,1):
            at=boundary+timedelta(microseconds=offset)
            start_year=2024 if offset>=0 else 2023
            add(f'boundary-{month}-{offset}',at.isoformat(),1,month,date(start_year,month,1).isoformat(),
                (date(start_year+1,month,1)-timedelta(days=1)).isoformat())
        for day in (1,28):
            end_year=2023 if month>=9 else 2024
            add(f'custom-{month}-{day}','2024-06-15T12:00:00+00:00',1,9,'2023-09-01',date(end_year,month,day).isoformat(),custom=True,endday=day,endmonth=month)
    add('positive-offset','2024-09-01T00:00:00+14:00',1,9,'2023-09-01','2024-08-31')
    add('negative-offset','2024-08-31T23:00:00-02:00',1,9,'2024-09-01','2025-08-31')
    add('positive-offset-year','2025-01-01T00:00:00+14:00',1,1,'2024-01-01','2024-12-31')
    add('negative-offset-year','2024-12-31T23:00:00-02:00',1,1,'2024-01-01','2024-12-31')
    add('date-object','2024-06-15',1,1,'2024-01-01','2024-12-31',kind='date')
    add('naive-datetime','2024-06-15T12:00:00',1,1,'2024-01-01','2024-12-31',kind='naive')
    add('zero-custom-day','2024-06-15T12:00:00Z',1,1,'2024-01-01','2024-12-31',custom=True,endday=0,endmonth=13)
    add('zero-custom-month','2024-06-15T12:00:00Z',1,1,'2024-01-01','2024-12-31',custom=True,endday=32,endmonth=0)
    add('disabled-custom','2024-06-15T12:00:00Z',1,1,'2024-01-01','2024-12-31',endday=32,endmonth=13)
    for name,created,day,month,message,custom,endday,endmonth in (
        ('bad-month','2024-06-15T12:00:00Z',1,13,'Month must be between 1 and 12',False,0,0),
        ('bad-day','2024-06-15T12:00:00Z',32,1,'Day must be between 1 and 31',False,0,0),
        ('bad-current-leap','2023-06-15T12:00:00Z',29,2,'Invalid date combination: day is out of range for month',False,0,0),
        ('bad-prior-leap','2024-06-15T12:00:00Z',29,2,'day is out of range for month',False,0,0),
        ('bad-custom-leap','2024-06-15T12:00:00Z',1,9,'day is out of range for month',True,29,2),
        ('bad-custom-month','2024-06-15T12:00:00Z',1,1,'month must be in 1..12',True,1,13),
        ('first-year','0001-06-15T12:00:00Z',1,1,'year 0 is out of range',False,0,0),
        ('last-year','9999-06-15T12:00:00Z',1,1,'year 10000 is out of range',False,0,0),
    ): add(name,created,day,month,message=message,custom=custom,endday=endday,endmonth=endmonth)
    for row in ranges:
        value=date.fromisoformat(row['Created']) if row['Kind']=='date' else datetime.fromisoformat(row['Created'])
        rule=row['Rule']
        user=SimpleNamespace(custom_end_date=rule['CustomEnd'],subscription_end_day=rule['EndDay'],subscription_end_month=rule['EndMonth'])
        try:
            first,last=BalanceSheetData.get_range_from_year_and_starting_date(value,rule['StartDay'],rule['StartMonth'],user)
        except ValueError as error:
            assert str(error)==row['Message'],(row['Name'],str(error),row['Message'])
        else:
            assert not row['Message'] and first==datetime.combine(date.fromisoformat(row['Start']),datetime.min.time(),timezone.utc) and last==datetime.combine(date.fromisoformat(row['End']),datetime.min.time(),timezone.utc),row['Name']
        if row['Kind']=='date': value=datetime.combine(value,datetime.min.time(),timezone.utc)
        elif value.tzinfo is None: value=value.replace(tzinfo=timezone.utc)
        row['Created']=value.isoformat()
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot()
    assert not Subscription._base_manager.exists()
    ct=ContentType.objects.get_for_model(Subscription)
    owner=User(user_id=uid(108000),username='date-policy-fixture',password='!fixture',role=1)
    User.original_objects.bulk_create([owner])
    association=SportAssociation(sport_association_id=uid(108001),user=owner,denomination='Date policy fixture',tax_code='00000000000')
    SportAssociation.original_objects.bulk_create([association])
    defaults=[]
    def civil(value): return None if value is None else dict(Year=value.year,Month=value.month,Day=value.day)
    created=datetime(2024,2,29,23,59,tzinfo=timezone.utc)
    try:
        specs=[]
        for duration in (0,1,2,3,4,5,99):
            for mask in range(4):
                initial_start=date(1990,1,2) if mask&1 else None
                initial_end=date(2070,3,4) if mask&2 else None
                expected_start=initial_start or ({1:date(2024,2,29),2:date(2023,9,1),3:date(2024,2,29)}.get(duration))
                expected_end=initial_end or ({1:date(2025,2,28),2:date(2024,8,31),3:date(2024,8,31)}.get(duration))
                specs.append((f'duration-{duration}-mask-{mask}',created,duration,False,0,0,initial_start,initial_end,expected_start,expected_end))
        for duration in (1,2,3,4):
            expected_start={1:date(2024,2,29),2:date(2023,9,1),3:date(2024,2,29)}.get(duration)
            expected_end={1:date(2025,2,28),2:date(2024,6,30),3:date(2024,6,30)}.get(duration)
            specs.append((f'custom-june-{duration}',created,duration,True,30,6,None,None,expected_start,expected_end))
        for duration in (2,3):
            expected_start=date(2023,9,1) if duration==2 else date(2024,2,29)
            specs.append((f'custom-equality-{duration}',created,duration,True,1,9,None,None,expected_start,date(2023,9,1)))
        specs.append(('offset-civil-date',datetime.fromisoformat('2024-03-01T00:30:00+14:00'),1,False,0,0,None,None,date(2024,3,1),date(2025,3,1)))
        specs.append(('naive-creation-date',datetime(2024,2,29,23,59),1,False,0,0,None,None,date(2024,2,29),date(2025,2,28)))
        for name,creation,duration,custom,endday,endmonth,initial_start,initial_end,expected_start,expected_end in specs:
            owner.subscription_start_day=1;owner.subscription_start_month=9;owner.subscription_duration=duration
            owner.custom_end_date=custom;owner.subscription_end_day=endday;owner.subscription_end_month=endmonth
            sub=Subscription(subscription_id=uid(108100+len(defaults)),sport_association=association,user=owner,
                creation_date=creation,start_date=initial_start,end_date=initial_end)
            Subscription._base_manager.bulk_create([sub])
            before=dict(Subscription._base_manager.filter(pk=sub.pk).values().get())
            sub.save()
            after=dict(Subscription._base_manager.filter(pk=sub.pk).values().get())
            assert after==dict(before,start_date=expected_start,end_date=expected_end),(name,'model save changed unexpected fields')
            changes={field:[str(old),str(new)] for field,old,new in (('start_date',initial_start,expected_start),('end_date',initial_end,expected_end)) if old!=new}
            logs=list(LogEntry.objects.filter(content_type=ct,object_pk=str(sub.pk)))
            assert len(logs)==int(bool(changes)),name
            if logs:
                assert logs[0].action==LogEntry.Action.UPDATE and logs[0].changes==changes,(name,logs[0].changes,changes)
                index=AuditLogIndex.objects.get(log_entry=logs[0])
                assert str(index.sport_association_id)==str(association.pk) and index.resolution_path=='direct'
            go_creation=creation if creation.tzinfo is not None else creation.replace(tzinfo=timezone.utc)
            defaults.append(dict(Name=name,Created=go_creation.isoformat(),
                Rule=dict(Season=dict(StartDay=1,StartMonth=9,CustomEnd=custom,EndDay=endday,EndMonth=endmonth),Duration=duration),
                Initial=dict(Start=civil(initial_start),End=civil(initial_end)),Expected=dict(Start=civil(expected_start),End=civil(expected_end))))
        # Validation runs even when neither date needs a default.
        owner.subscription_start_month=0
        sub.start_date=date(1990,1,2);sub.end_date=date(2070,3,4)
        before=snapshot()
        try: sub.save()
        except ValueError as error: assert str(error)=='Month must be between 1 and 12'
        else: raise AssertionError('existing dates bypassed calendar validation')
        assert snapshot()==before
        # Characterize the real automatic archive task before correcting it.
        User.original_objects.filter(pk=owner.pk).update(auto_archive=True)
        archive_errors=[]
        for clock_value,expected_error in ((datetime(2030,1,1),"can't compare offset-naive and offset-aware datetimes"),
                                           (datetime(2030,1,1,tzinfo=timezone.utc),"has no attribute 'id'")):
            before=snapshot();captured=io.StringIO()
            with patch.object(tasks,'datetime',wraps=datetime) as clock,redirect_stdout(captured):
                clock.now.return_value=clock_value
                assert tasks.auto_archive_subscription.run() is None
            assert expected_error in captured.getvalue(),captured.getvalue()
            assert snapshot()==before,'broken automatic archive changed managed records'
            archive_errors.append(expected_error)
        fixture=work/'subscription-dates.json'
        fixture.write_text(json.dumps(dict(Ranges=ranges,Defaults=defaults)))
        (output/'django-oracle.json').write_text(json.dumps(dict(success=True,range_cases=len(ranges),model_default_cases=len(defaults),
            auto_archive_errors=archive_errors,scope='calendar/default calculation; complete save/archive parity remains open'),indent=2)+'\n')
        print(f'Django calendar: {len(ranges)} explicit range cases, {len(defaults)} actual model saves and two automatic-archive failures verified',flush=True)
        completed=subprocess.run(['go','test','-race','-tags=subscriptiondatesintegration','./internal/service','-run','^TestSubscriptionDatesDjangoOracle$','-json'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_SUBSCRIPTION_DATES_FIXTURE':str(fixture)},capture_output=True,text=True,timeout=120)
        (output/'go-test.jsonl').write_text(completed.stdout)
        events=[json.loads(line) for line in completed.stdout.splitlines()]
        for event in events:
            if event.get('Action')=='fail' or ('Output' in event and ('FAIL' in event['Output'] or 'Error' in event['Output'])):print(event.get('Output',event),flush=True)
        if completed.stderr:print(completed.stderr)
        completed.check_returncode()
        expected={f"TestSubscriptionDatesDjangoOracle/{kind}/{row['Name']}" for kind,rows in (('range',ranges),('defaults',defaults)) for row in rows}
        passed={e.get('Test') for e in events if e['Action']=='pass' and e.get('Test','').count('/')==2}
        assert passed==expected and not any(e['Action']=='skip' for e in events),'missing or skipped calendar case'
        result=dict(success=True,range_cases=len(ranges),model_default_cases=len(defaults),scope='calendar/default calculation; no Go archive/save activation')
        (output/'body-results.json').write_text(json.dumps(result,indent=2)+'\n')
        return len(ranges)+len(defaults)
    finally:
        with disable_auditlog():
            LogEntry.objects.filter(content_type=ct,object_pk__in=[str(pk) for pk in Subscription._base_manager.values_list('pk',flat=True)]).delete()
            Subscription._base_manager.all().delete()
            SportAssociation.original_objects.filter(pk=association.pk).delete()
            User.original_objects.filter(pk=owner.pk).delete()
        assert snapshot()==original,'date fixture changed preexisting managed state'
