# -*- coding: utf-8 -*-
from south.utils import datetime_utils as datetime
from south.db import db
from south.v2 import DataMigration
from django.db import models
from django.utils import timezone, tzinfo
import pytz            
from datetime import datetime
from datetime import timedelta

from reports.models import UserProfile, UserGroup, Permission, ApiLog, Vocabularies
from reports.utils.sqlalchemy_bridge import Bridge

from db.support.data_converter import default_converter

import json
import logging
from sqlalchemy.sql.expression import select, text, desc
from sqlalchemy.sql.elements import literal_column
from sqlalchemy.sql.operators import asc_op
import sys
import os
import csv
from lims.base_settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

class Migration(DataMigration):

    def forwards(self, orm):
        
        # Steps: convert checklist_item / checklist_item_event into 
        # user_checklist_item
        
        # create a separate vocab file: checklist_item_vocab, add to api_init.csv
        # output vocabs into a vocabulary patch file
        vocab_file = os.path.join(
            PROJECT_ROOT, '..',
            'db','static','api_init','vocabularies_checklists_data.csv')
        logger.info('write vocabularies to %s' % vocab_file)
        resource_uri = '/reports/api/v1/vocabularies/%s/%s/'
        with open(vocab_file,'w') as _file:
            vocab_writer = csv.writer(_file)
            header = ['resource_uri','key','scope','ordinal','title'] 
            vocab_writer.writerow(header)

            ci_group_map = {}
            scope = 'checklistitem.group'
            default_order = ['mailing', 'forms', 'non-harvard', 'imaging','legacy']
            for obj in orm.ChecklistItem.objects.all().distinct('checklist_item_group'):
                key = default_converter(obj.checklist_item_group)
                ordinal = 0
                for i,x in enumerate(default_order):
                    if x in obj.checklist_item_group.lower():
                        ordinal=i 
                        break
                    
                v = Vocabularies.objects.create(
                    scope=scope,
                    key=key,
                    title=obj.checklist_item_group,
                    ordinal=ordinal
                    )
                v.save()
                vocab_writer.writerow([
                    resource_uri % (v.scope,v.key),
                    v.key,v.scope,v.ordinal,v.title])
                ci_group_map[obj.checklist_item_group] = key 
                logger.info(str(('created', v)))
    
            scope = 'checklistitem.%s.name'
            ci_name_map = {}
            for obj in orm.ChecklistItem.objects.all().distinct('item_name'):
                key = default_converter(obj.item_name)
                _scope = scope % default_converter(obj.checklist_item_group)
                v = Vocabularies.objects.create(
                    scope=_scope,
                    key=key,
                    title=obj.item_name,
                    ordinal=obj.order_statistic )
                v.save()
                vocab_writer.writerow([
                    resource_uri % (v.scope,v.key),
                    v.key,v.scope,v.ordinal,v.title])
                ci_name_map[obj.item_name] = key
                logger.info(str(('created', v)))
                
            scope = 'checklistitem.status'
            status_values = [
                {
                    'key': 'not_completed',
                    'title': 'Not Completed',
                    'ordinal': 0
                },
                {
                    'key': 'activated',
                    'title': 'Activated',
                    'ordinal': 1
                },
                {
                    'key': 'deactivated',
                    'title': 'Deactivated',
                    'ordinal': 2
                },
                {
                    'key': 'na',
                    'title': 'N/A',
                    'ordinal': 3
                },
                {
                    'key': 'completed',
                    'title': 'Completed',
                    'ordinal': 4
                },
            ]
            
            for _dict in status_values:
                _dict['scope']=scope
                v = Vocabularies(**_dict)
                v.save()
                vocab_writer.writerow([
                    resource_uri % (v.scope,v.key),
                    v.key,v.scope,v.ordinal,v.title])
                logger.info(str(('created', v)))
    
        logger.info('vocabulary creation done')

        # create entries in the user_checklist_item table
        sql_keys = [
            'suid','cigroup','ciname',
            'su_username','admin_username','admin_suid','admin_upid',
            'date_performed', 'date_created','status',
            ]
        sql = '''
select
screening_room_user_id,
ci.checklist_item_group,
ci.item_name,
su.username su_username,
admin.username admin_username,
admin.screensaver_user_id admin_suid,
up.id admin_upid,
cie.date_performed,
cie.date_created,
case when cie.is_not_applicable then 'n_a'
     when ci.is_expirable and cie.date_performed is not null then
    case when cie.is_expiration then 'deactivated' else 'activated' end
     when cie.date_performed is not null then 'completed'     
     else 'not_completed'
     end as status
from checklist_item ci
join checklist_item_event cie using(checklist_item_id)
join screensaver_user su on screening_room_user_id=su.screensaver_user_id
join screensaver_user admin on cie.created_by_id=admin.screensaver_user_id
left join reports_userprofile up on up.id=admin.user_id
order by screening_room_user_id, checklist_item_group, item_name, cie.date_performed asc;
'''

        bridge = Bridge()
        conn = bridge.get_engine().connect()

        log_ref_resource_name = 'userchecklistitem'
        
        _dict = None
        log = None
        
        uci_hash = {}
        unique_log_keys = set()
        try:
                    
            _list = db.execute(sql)
            i = 0
            for row in _list:
                _dict = dict(zip(sql_keys,row))
                
                key = '/'.join([str(_dict['suid']),_dict['cigroup'],_dict['ciname']])
                previous_dict = uci_hash.get(key)
                logger.info('prev_dict: %s:%s' % (key,previous_dict))
                if previous_dict:
                    uci = previous_dict['obj']
                    uci.admin_user_id = int(_dict['admin_suid'])
                    uci.status = _dict['status']
                    uci.status_date = _dict['date_performed']
                    uci.save()
                    logger.info('updated: %r' % uci)
                    
                    
                    
                    
                    
#                 # Make an effort to find the previous activity log
#                 # ** only if deactivated **
#                 previous_log = None
#                 if  _dict['status'] in ['deactivated','activated']:
#                     stmt = ( 
#                         select([text('*')])
#                             .select_from(activity_query)
#                             .where(literal_column('aquery.screensaver_user_id')==int(_dict['suid']))
#                             .where(literal_column('aquery.date_created')<_dict['date_performed'])
#                             .where(literal_column('aquery.comments').ilike('%{value}%'.format(value=_dict['ciname'])))
#                             .order_by(desc(literal_column('aquery.date_created')))
#                         )
#                     
#                     #             logger.info(str(('stmt',str(stmt.compile(compile_kwargs={"literal_binds": True}) ))))
#                     resultset = conn.execute(stmt)
#                     for x in resultset:
#                         if _dict['ciname'] in x.comments:
#                             if ( _dict['status'] == 'activated'
#                                 and 'deactivated' in x.comments ):
#                                 previous_log = x
#                             elif ( _dict['status'] == 'deactivated'
#                                 and 'activated' in x.comments ):
#                                 previous_log = x
                    
                    
                    
                    
                    
                    
                    
                    
                    
                    
                    
                    
                    
                    
                    
                else:
                    uci_hash[key] = _dict
                    logger.debug(str(('create user checklist item', _dict, 
                        _dict['date_performed'].isoformat())))
                    uci = orm.UserChecklistItem.objects.create(
                        screensaver_user_id = int(_dict['suid']),
                        admin_user_id = int(_dict['admin_suid']),
                        item_group = ci_group_map[_dict['cigroup']],
                        item_name = ci_name_map[_dict['ciname']],
                        status = _dict['status'],
                        status_date = _dict['date_performed'])
                    uci.save()
                    _dict['obj'] = uci
                    
                    logger.info('created: %r, %s' % (uci, _dict))
                    i += 1

                date_time = pytz.utc.localize(_dict['date_created'])                
                if date_time.date() != _dict['date_performed']:
                    # only use the less accurate date_performed date if that date
                    # is not equal to the date_created date
                    date_time = pytz.utc.localize(
                        datetime.combine(_dict['date_performed'],datetime.min.time()))
                # create the apilog for this item
                log = ApiLog()
                log.ref_resource_name = log_ref_resource_name
                log.key = '/'.join([_dict['su_username'],uci.item_group,uci.item_name])
                log.username = _dict['admin_username']
                log.user_id = _dict['admin_upid']
                log.date_time = date_time
                log.api_action = 'PATCH'
                log.uri = '/'.join([log.ref_resource_name,log.key])
                log.comment = 'status=%s' % _dict['status']
                
                # is the key (date_time, actually) unique?
                full_key = '/'.join([log.ref_resource_name,log.key,str(log.date_time)])
                while full_key in unique_log_keys:
                    # add a second to make it unique; because date performed is a date,
                    # there are collisions
                    log.date_time = log.date_time  + timedelta(0,1)
                    full_key = '/'.join([log.ref_resource_name,log.key,str(log.date_time)])
                    
                unique_log_keys.add(full_key)
                if previous_dict:
                    diff_keys = ['status']
                    diffs = {}
                    logger.info(str(('found previous_dict', previous_dict)))
                    diff_keys.append('admin_username')
                    diffs['admin_username'] = [previous_dict['admin_username'], _dict['admin_username']]
                    
                    diff_keys.append('status_date')
                    diffs['status_date'] = [
                        previous_dict['date_performed'].isoformat(), 
                        _dict['date_performed'].isoformat()]
                    
                    diffs['status'] = [previous_dict['status'],_dict['status']]
#                 if _dict['status'] == 'activated':
#                     if previous_log:
#                         diffs['status'] = ['deactivated','activated']
#                 if _dict['status'] == 'deactivated':
#                     diffs['status'] = ['activated','deactivated']
                
                    log.diff_keys = json.dumps(diff_keys)
                    log.diffs = json.dumps(diffs)
     
                logger.debug(str(('create log', log)))
                
                log.save()
                log = None
                if i%100 == 0:
                    logger.info(str(('created', i, 'logs')))
        except Exception, e:
            logger.error('migration exc', exc_info=sys.exc_info())
            raise e  

        print 'created %d user_checklist_items' % i
        

    def backwards(self, orm):
        "Write your backwards methods here."

    models = {
        u'auth.group': {
            'Meta': {'object_name': 'Group'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'unique': 'True', 'max_length': '80'}),
            'permissions': ('django.db.models.fields.related.ManyToManyField', [], {'to': u"orm['auth.Permission']", 'symmetrical': 'False', 'blank': 'True'})
        },
        u'auth.permission': {
            'Meta': {'ordering': "(u'content_type__app_label', u'content_type__model', u'codename')", 'unique_together': "((u'content_type', u'codename'),)", 'object_name': 'Permission'},
            'codename': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'content_type': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['contenttypes.ContentType']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '50'})
        },
        u'auth.user': {
            'Meta': {'object_name': 'User'},
            'date_joined': ('django.db.models.fields.DateTimeField', [], {'default': 'datetime.datetime.now'}),
            'email': ('django.db.models.fields.EmailField', [], {'max_length': '75', 'blank': 'True'}),
            'first_name': ('django.db.models.fields.CharField', [], {'max_length': '30', 'blank': 'True'}),
            'groups': ('django.db.models.fields.related.ManyToManyField', [], {'symmetrical': 'False', 'related_name': "u'user_set'", 'blank': 'True', 'to': u"orm['auth.Group']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'is_active': ('django.db.models.fields.BooleanField', [], {'default': 'True'}),
            'is_staff': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'is_superuser': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'last_login': ('django.db.models.fields.DateTimeField', [], {'default': 'datetime.datetime.now'}),
            'last_name': ('django.db.models.fields.CharField', [], {'max_length': '30', 'blank': 'True'}),
            'password': ('django.db.models.fields.CharField', [], {'max_length': '128'}),
            'user_permissions': ('django.db.models.fields.related.ManyToManyField', [], {'symmetrical': 'False', 'related_name': "u'user_set'", 'blank': 'True', 'to': u"orm['auth.Permission']"}),
            'username': ('django.db.models.fields.CharField', [], {'unique': 'True', 'max_length': '30'})
        },
        u'contenttypes.contenttype': {
            'Meta': {'ordering': "('name',)", 'unique_together': "(('app_label', 'model'),)", 'object_name': 'ContentType', 'db_table': "'django_content_type'"},
            'app_label': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'model': ('django.db.models.fields.CharField', [], {'max_length': '100'}),
            'name': ('django.db.models.fields.CharField', [], {'max_length': '100'})
        },
        u'db.abasetestset': {
            'Meta': {'object_name': 'AbaseTestset', 'db_table': "u'abase_testset'"},
            'abase_testset_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'comments': ('django.db.models.fields.TextField', [], {}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"}),
            'testset_date': ('django.db.models.fields.DateField', [], {}),
            'testset_name': ('django.db.models.fields.TextField', [], {}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.activity': {
            'Meta': {'object_name': 'Activity', 'db_table': "u'activity'"},
            'activity_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'comments': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'created_by': ('django.db.models.fields.related.ForeignKey', [], {'blank': 'True', 'related_name': "u'activities_created'", 'null': 'True', 'to': u"orm['db.ScreensaverUser']"}),
            'date_created': ('django.db.models.fields.DateTimeField', [], {}),
            'date_loaded': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_of_activity': ('django.db.models.fields.DateField', [], {}),
            'date_publicly_available': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'performed_by': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "u'activities_performed'", 'to': u"orm['db.ScreensaverUser']"}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.activityupdateactivity': {
            'Meta': {'object_name': 'ActivityUpdateActivity', 'db_table': "u'activity_update_activity'"},
            'activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Activity']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'update_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']"})
        },
        u'db.administrativeactivity': {
            'Meta': {'object_name': 'AdministrativeActivity', 'db_table': "u'administrative_activity'"},
            'activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Activity']", 'primary_key': 'True'}),
            'administrative_activity_type': ('django.db.models.fields.TextField', [], {})
        },
        u'db.administratoruser': {
            'Meta': {'object_name': 'AdministratorUser', 'db_table': "u'administrator_user'"},
            'screensaver_user': ('django.db.models.fields.related.OneToOneField', [], {'to': u"orm['db.ScreensaverUser']", 'unique': 'True', 'primary_key': 'True'})
        },
        u'db.annotationtype': {
            'Meta': {'object_name': 'AnnotationType', 'db_table': "u'annotation_type'"},
            'annotation_type_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'description': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'is_numeric': ('django.db.models.fields.BooleanField', [], {}),
            'name': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'ordinal': ('django.db.models.fields.IntegerField', [], {}),
            'study': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.annotationvalue': {
            'Meta': {'object_name': 'AnnotationValue', 'db_table': "u'annotation_value'"},
            'annotation_type': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AnnotationType']", 'null': 'True', 'blank': 'True'}),
            'annotation_value_id': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'numeric_value': ('django.db.models.fields.FloatField', [], {'null': 'True', 'blank': 'True'}),
            'reagent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Reagent']", 'null': 'True', 'blank': 'True'}),
            'value': ('django.db.models.fields.TextField', [], {'blank': 'True'})
        },
        u'db.assayplate': {
            'Meta': {'object_name': 'AssayPlate', 'db_table': "u'assay_plate'"},
            'assay_plate_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'library_screening': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.LibraryScreening']", 'null': 'True', 'blank': 'True'}),
            'plate': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Plate']", 'null': 'True', 'blank': 'True'}),
            'plate_number': ('django.db.models.fields.IntegerField', [], {}),
            'replicate_ordinal': ('django.db.models.fields.IntegerField', [], {}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"}),
            'screen_result_data_loading': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']", 'null': 'True', 'blank': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.assaywell': {
            'Meta': {'object_name': 'AssayWell', 'db_table': "u'assay_well'"},
            'assay_well_control_type': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'assay_well_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'confirmed_positive_value': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'is_positive': ('django.db.models.fields.BooleanField', [], {}),
            'screen_result': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreenResult']"}),
            'version': ('django.db.models.fields.IntegerField', [], {}),
            'well': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Well']"})
        },
        u'db.attachedfile': {
            'Meta': {'object_name': 'AttachedFile', 'db_table': "u'attached_file'"},
            'attached_file_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'attached_file_type': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AttachedFileType']"}),
            'created_by': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']", 'null': 'True', 'blank': 'True'}),
            'date_created': ('django.db.models.fields.DateTimeField', [], {}),
            'date_loaded': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_publicly_available': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'file_contents': ('django.db.models.fields.TextField', [], {}),
            'file_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'filename': ('django.db.models.fields.TextField', [], {}),
            'reagent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Reagent']", 'null': 'True', 'blank': 'True'}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']", 'null': 'True', 'blank': 'True'}),
            'screensaver_user': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreeningRoomUser']", 'null': 'True', 'blank': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.attachedfiletype': {
            'Meta': {'object_name': 'AttachedFileType', 'db_table': "u'attached_file_type'"},
            'attached_file_type_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'for_entity_type': ('django.db.models.fields.CharField', [], {'max_length': '31'}),
            'value': ('django.db.models.fields.TextField', [], {})
        },
        u'db.attachedfileupdateactivity': {
            'Meta': {'object_name': 'AttachedFileUpdateActivity', 'db_table': "u'attached_file_update_activity'"},
            'attached_file': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AttachedFile']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'update_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']"})
        },
        u'db.cachedquery': {
            'Meta': {'object_name': 'CachedQuery', 'db_table': "u'cached_query'"},
            'count': ('django.db.models.fields.IntegerField', [], {'null': 'True'}),
            'datetime': ('django.db.models.fields.DateTimeField', [], {'default': 'datetime.datetime.now'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'key': ('django.db.models.fields.TextField', [], {'unique': 'True'}),
            'params': ('django.db.models.fields.TextField', [], {'null': 'True'}),
            'sql': ('django.db.models.fields.TextField', [], {}),
            'uri': ('django.db.models.fields.TextField', [], {}),
            'username': ('django.db.models.fields.CharField', [], {'max_length': '128'})
        },
        u'db.cellline': {
            'Meta': {'object_name': 'CellLine', 'db_table': "u'cell_line'"},
            'cell_line_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'value': ('django.db.models.fields.TextField', [], {'unique': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.checklistitem': {
            'Meta': {'object_name': 'ChecklistItem', 'db_table': "u'checklist_item'"},
            'checklist_item_group': ('django.db.models.fields.TextField', [], {}),
            'checklist_item_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'is_expirable': ('django.db.models.fields.BooleanField', [], {}),
            'item_name': ('django.db.models.fields.TextField', [], {'unique': 'True'}),
            'order_statistic': ('django.db.models.fields.IntegerField', [], {}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.checklistitemevent': {
            'Meta': {'object_name': 'ChecklistItemEvent', 'db_table': "u'checklist_item_event'"},
            'checklist_item_event_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'checklist_item_id': ('django.db.models.fields.IntegerField', [], {}),
            'created_by': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']", 'null': 'True', 'blank': 'True'}),
            'date_created': ('django.db.models.fields.DateTimeField', [], {}),
            'date_loaded': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_performed': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'date_publicly_available': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'is_expiration': ('django.db.models.fields.BooleanField', [], {}),
            'is_not_applicable': ('django.db.models.fields.BooleanField', [], {}),
            'screening_room_user': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreeningRoomUser']"})
        },
        u'db.checklistitemeventupdateactivity': {
            'Meta': {'object_name': 'ChecklistItemEventUpdateActivity', 'db_table': "u'checklist_item_event_update_activity'"},
            'checklist_item_event': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ChecklistItemEvent']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'update_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']"})
        },
        u'db.cherrypickassayplate': {
            'Meta': {'unique_together': "((u'cherry_pick_request', u'plate_ordinal', u'attempt_ordinal'),)", 'object_name': 'CherryPickAssayPlate', 'db_table': "u'cherry_pick_assay_plate'"},
            'assay_plate_type': ('django.db.models.fields.TextField', [], {}),
            'attempt_ordinal': ('django.db.models.fields.IntegerField', [], {}),
            'cherry_pick_assay_plate_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'cherry_pick_assay_plate_type': ('django.db.models.fields.CharField', [], {'max_length': '31'}),
            'cherry_pick_liquid_transfer': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickLiquidTransfer']", 'null': 'True', 'blank': 'True'}),
            'cherry_pick_request': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickRequest']"}),
            'legacy_plate_name': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'plate_ordinal': ('django.db.models.fields.IntegerField', [], {}),
            'status': ('django.db.models.fields.TextField', [], {'null': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.cherrypickassayplatescreeninglink': {
            'Meta': {'object_name': 'CherryPickAssayPlateScreeningLink', 'db_table': "u'cherry_pick_assay_plate_screening_link'"},
            'cherry_pick_assay_plate': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickAssayPlate']"}),
            'cherry_pick_screening': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickScreening']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'})
        },
        u'db.cherrypickliquidtransfer': {
            'Meta': {'object_name': 'CherryPickLiquidTransfer', 'db_table': "u'cherry_pick_liquid_transfer'"},
            'activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.LabActivity']", 'primary_key': 'True'}),
            'status': ('django.db.models.fields.TextField', [], {})
        },
        u'db.cherrypickrequest': {
            'Meta': {'object_name': 'CherryPickRequest', 'db_table': "u'cherry_pick_request'"},
            'assay_plate_type': ('django.db.models.fields.TextField', [], {}),
            'assay_protocol_comments': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'cherry_pick_assay_protocols_followed': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'cherry_pick_followup_results_status': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'cherry_pick_request_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'comments': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'created_by': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']", 'null': 'True', 'blank': 'True'}),
            'date_created': ('django.db.models.fields.DateTimeField', [], {}),
            'date_loaded': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_publicly_available': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_requested': ('django.db.models.fields.DateField', [], {}),
            'date_volume_approved': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'is_randomized_assay_plate_layout': ('django.db.models.fields.BooleanField', [], {}),
            'keep_source_plate_cherry_picks_together': ('django.db.models.fields.BooleanField', [], {}),
            'legacy_cherry_pick_request_number': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'max_skipped_wells_per_plate': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'number_unfulfilled_lab_cherry_picks': ('django.db.models.fields.IntegerField', [], {}),
            'requested_by': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreeningRoomUser']"}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"}),
            'transfer_volume_per_well_approved': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '9', 'blank': 'True'}),
            'transfer_volume_per_well_requested': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '9', 'blank': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {}),
            'volume_approved_by': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministratorUser']", 'null': 'True', 'blank': 'True'})
        },
        u'db.cherrypickrequestemptywell': {
            'Meta': {'object_name': 'CherryPickRequestEmptyWell', 'db_table': "u'cherry_pick_request_empty_well'"},
            'cherry_pick_request': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickRequest']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'well_name': ('django.db.models.fields.CharField', [], {'max_length': '255', 'blank': 'True'})
        },
        u'db.cherrypickrequestupdateactivity': {
            'Meta': {'object_name': 'CherryPickRequestUpdateActivity', 'db_table': "u'cherry_pick_request_update_activity'"},
            'cherry_pick_request': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickRequest']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'update_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']", 'unique': 'True'})
        },
        u'db.cherrypickscreening': {
            'Meta': {'object_name': 'CherryPickScreening', 'db_table': "u'cherry_pick_screening'"},
            'activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screening']", 'primary_key': 'True'}),
            'cherry_pick_request': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickRequest']"})
        },
        u'db.collaboratorlink': {
            'Meta': {'object_name': 'CollaboratorLink', 'db_table': "u'collaborator_link'"},
            'collaborator': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreeningRoomUser']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"})
        },
        u'db.copy': {
            'Meta': {'object_name': 'Copy', 'db_table': "u'copy'"},
            'comments': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'copy_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'created_by': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']", 'null': 'True', 'blank': 'True'}),
            'date_created': ('django.db.models.fields.DateTimeField', [], {}),
            'date_loaded': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_plated': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'date_publicly_available': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'library': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Library']"}),
            'max_mg_ml_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '5', 'decimal_places': '3', 'blank': 'True'}),
            'max_molar_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '13', 'decimal_places': '12', 'blank': 'True'}),
            'min_mg_ml_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '5', 'decimal_places': '3', 'blank': 'True'}),
            'min_molar_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '13', 'decimal_places': '12', 'blank': 'True'}),
            'name': ('django.db.models.fields.TextField', [], {}),
            'plate_locations_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'plates_available': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'primary_plate_location_id': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'primary_plate_status': ('django.db.models.fields.TextField', [], {}),
            'primary_well_mg_ml_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '5', 'decimal_places': '3', 'blank': 'True'}),
            'primary_well_molar_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '13', 'decimal_places': '12', 'blank': 'True'}),
            'usage_type': ('django.db.models.fields.TextField', [], {}),
            'version': ('django.db.models.fields.IntegerField', [], {}),
            'well_concentration_dilution_factor': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '8', 'decimal_places': '2', 'blank': 'True'})
        },
        u'db.copyupdateactivity': {
            'Meta': {'object_name': 'CopyUpdateActivity', 'db_table': "u'copy_update_activity'"},
            'copy_id': ('django.db.models.fields.IntegerField', [], {}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'update_activity_id': ('django.db.models.fields.IntegerField', [], {'unique': 'True'})
        },
        u'db.copywell': {
            'Meta': {'object_name': 'CopyWell', 'db_table': "u'copy_well'"},
            'adjustments': ('django.db.models.fields.IntegerField', [], {}),
            'copy': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Copy']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'initial_volume': ('django.db.models.fields.FloatField', [], {'null': 'True', 'blank': 'True'}),
            'plate': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Plate']"}),
            'plate_number': ('django.db.models.fields.IntegerField', [], {}),
            'volume': ('django.db.models.fields.FloatField', [], {'null': 'True', 'blank': 'True'}),
            'well': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Well']"})
        },
        u'db.datacolumn': {
            'Meta': {'object_name': 'DataColumn', 'db_table': "u'data_column'"},
            'assay_phenotype': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'assay_readout_type': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'channel': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'comments': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'data_column_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'data_type': ('django.db.models.fields.TextField', [], {}),
            'decimal_places': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'description': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'how_derived': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'is_derived': ('django.db.models.fields.BooleanField', [], {}),
            'is_follow_up_data': ('django.db.models.fields.BooleanField', [], {}),
            'medium_positives_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'name': ('django.db.models.fields.TextField', [], {}),
            'ordinal': ('django.db.models.fields.IntegerField', [], {}),
            'positives_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'replicate_ordinal': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'screen_result': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreenResult']"}),
            'strong_positives_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'time_point': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'time_point_ordinal': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {}),
            'weak_positives_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'zdepth_ordinal': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'})
        },
        u'db.datacolumnderivedfromlink': {
            'Meta': {'object_name': 'DataColumnDerivedFromLink', 'db_table': "u'data_column_derived_from_link'"},
            'derived_data_column': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.DataColumn']"}),
            'derived_from_data_column': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "u'derived_from'", 'to': u"orm['db.DataColumn']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'})
        },
        u'db.equipmentused': {
            'Meta': {'object_name': 'EquipmentUsed', 'db_table': "u'equipment_used'"},
            'description': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'equipment': ('django.db.models.fields.TextField', [], {}),
            'equipment_used_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'lab_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.LabActivity']"}),
            'protocol': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.fundingsupport': {
            'Meta': {'object_name': 'FundingSupport', 'db_table': "u'funding_support'"},
            'funding_support_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'value': ('django.db.models.fields.TextField', [], {'unique': 'True', 'blank': 'True'})
        },
        u'db.gene': {
            'Meta': {'object_name': 'Gene', 'db_table': "u'gene'"},
            'entrezgene_id': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'gene_id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'gene_name': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'species_name': ('django.db.models.fields.TextField', [], {'blank': 'True'})
        },
        u'db.genegenbankaccessionnumber': {
            'Meta': {'unique_together': "((u'gene', u'genbank_accession_number'),)", 'object_name': 'GeneGenbankAccessionNumber', 'db_table': "u'gene_genbank_accession_number'"},
            'genbank_accession_number': ('django.db.models.fields.TextField', [], {}),
            'gene': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Gene']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'})
        },
        u'db.genesymbol': {
            'Meta': {'unique_together': "((u'gene', u'ordinal'),)", 'object_name': 'GeneSymbol', 'db_table': "u'gene_symbol'"},
            'entrezgene_symbol': ('django.db.models.fields.TextField', [], {}),
            'gene': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Gene']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'ordinal': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.labactivity': {
            'Meta': {'object_name': 'LabActivity', 'db_table': "u'lab_activity'"},
            'activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Activity']", 'primary_key': 'True'}),
            'molar_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '13', 'decimal_places': '12', 'blank': 'True'}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"}),
            'volume_transferred_per_well_from_library_plates': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '9', 'blank': 'True'})
        },
        u'db.labaffiliation': {
            'Meta': {'object_name': 'LabAffiliation', 'db_table': "u'lab_affiliation'"},
            'affiliation_category': ('django.db.models.fields.TextField', [], {}),
            'affiliation_name': ('django.db.models.fields.TextField', [], {'unique': 'True'}),
            'lab_affiliation_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.labcherrypick': {
            'Meta': {'object_name': 'LabCherryPick', 'db_table': "u'lab_cherry_pick'"},
            'assay_plate_column': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'assay_plate_row': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'cherry_pick_assay_plate': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickAssayPlate']", 'null': 'True', 'blank': 'True'}),
            'cherry_pick_request': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickRequest']"}),
            'lab_cherry_pick_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'screener_cherry_pick': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreenerCherryPick']"}),
            'source_well': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Well']"}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.labhead': {
            'Meta': {'object_name': 'LabHead', 'db_table': "u'lab_head'"},
            'lab_affiliation': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.LabAffiliation']", 'null': 'True', 'blank': 'True'}),
            'screensaver_user': ('django.db.models.fields.related.OneToOneField', [], {'to': u"orm['db.ScreeningRoomUser']", 'unique': 'True', 'primary_key': 'True'})
        },
        u'db.legacysmallmoleculecasnumber': {
            'Meta': {'object_name': 'LegacySmallMoleculeCasNumber', 'db_table': "u'_legacy_small_molecule_cas_number'"},
            'cas_number': ('django.db.models.fields.TextField', [], {}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'smiles': ('django.db.models.fields.CharField', [], {'max_length': '2047'})
        },
        u'db.library': {
            'Meta': {'object_name': 'Library', 'db_table': "u'library'"},
            'created_by': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']", 'null': 'True', 'blank': 'True'}),
            'date_created': ('django.db.models.fields.DateTimeField', [], {}),
            'date_loaded': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_publicly_available': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_received': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'date_screenable': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'description': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'end_plate': ('django.db.models.fields.IntegerField', [], {'unique': 'True'}),
            'experimental_well_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'is_pool': ('django.db.models.fields.NullBooleanField', [], {'null': 'True', 'blank': 'True'}),
            'latest_released_contents_version_id': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'library_id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'library_name': ('django.db.models.fields.TextField', [], {'unique': 'True'}),
            'library_type': ('django.db.models.fields.TextField', [], {}),
            'loaded_by': ('django.db.models.fields.related.ForeignKey', [], {'blank': 'True', 'related_name': "u'libraries_loaded'", 'null': 'True', 'to': u"orm['db.ScreensaverUser']"}),
            'owner_screener': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreeningRoomUser']", 'null': 'True', 'blank': 'True'}),
            'plate_size': ('django.db.models.fields.TextField', [], {}),
            'provider': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'screen_type': ('django.db.models.fields.TextField', [], {}),
            'screening_status': ('django.db.models.fields.TextField', [], {}),
            'short_name': ('django.db.models.fields.TextField', [], {'unique': 'True'}),
            'solvent': ('django.db.models.fields.TextField', [], {}),
            'start_plate': ('django.db.models.fields.IntegerField', [], {'unique': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'version_number': ('django.db.models.fields.IntegerField', [], {'default': '0'})
        },
        u'db.librarycontentsversion': {
            'Meta': {'object_name': 'LibraryContentsVersion', 'db_table': "u'library_contents_version'"},
            'library': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Library']"}),
            'library_contents_loading_activity': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "u'lcv_load'", 'to': u"orm['db.AdministrativeActivity']"}),
            'library_contents_release_activity': ('django.db.models.fields.related.ForeignKey', [], {'blank': 'True', 'related_name': "u'lcv_release'", 'null': 'True', 'to': u"orm['db.AdministrativeActivity']"}),
            'library_contents_version_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {}),
            'version_number': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.libraryscreening': {
            'Meta': {'object_name': 'LibraryScreening', 'db_table': "u'library_screening'"},
            'abase_testset_id': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screening']", 'primary_key': 'True'}),
            'is_for_external_library_plates': ('django.db.models.fields.BooleanField', [], {}),
            'libraries_screened_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'library_plates_screened_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'screened_experimental_well_count': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.libraryupdateactivity': {
            'Meta': {'object_name': 'LibraryUpdateActivity', 'db_table': "u'library_update_activity'"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'library': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Library']"}),
            'update_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']"})
        },
        u'db.molfile': {
            'Meta': {'object_name': 'Molfile', 'db_table': "u'molfile'"},
            'molfile': ('django.db.models.fields.TextField', [], {}),
            'reagent': ('django.db.models.fields.related.OneToOneField', [], {'to': u"orm['db.Reagent']", 'unique': 'True', 'primary_key': 'True'})
        },
        u'db.naturalproductreagent': {
            'Meta': {'object_name': 'NaturalProductReagent', 'db_table': "u'natural_product_reagent'"},
            'reagent': ('django.db.models.fields.related.OneToOneField', [], {'to': u"orm['db.Reagent']", 'unique': 'True', 'primary_key': 'True'})
        },
        u'db.plate': {
            'Meta': {'object_name': 'Plate', 'db_table': "u'plate'"},
            'avg_remaining_volume': ('django.db.models.fields.FloatField', [], {'null': 'True', 'blank': 'True'}),
            'copy': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Copy']"}),
            'created_by': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']", 'null': 'True', 'blank': 'True'}),
            'date_created': ('django.db.models.fields.DateTimeField', [], {}),
            'date_loaded': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_publicly_available': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'facility_id': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'max_mg_ml_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '5', 'decimal_places': '3', 'blank': 'True'}),
            'max_molar_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '13', 'decimal_places': '12', 'blank': 'True'}),
            'max_remaining_volume': ('django.db.models.fields.FloatField', [], {'null': 'True', 'blank': 'True'}),
            'min_mg_ml_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '5', 'decimal_places': '3', 'blank': 'True'}),
            'min_molar_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '13', 'decimal_places': '12', 'blank': 'True'}),
            'min_remaining_volume': ('django.db.models.fields.FloatField', [], {'null': 'True', 'blank': 'True'}),
            'plate_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'plate_location': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.PlateLocation']", 'null': 'True', 'blank': 'True'}),
            'plate_number': ('django.db.models.fields.IntegerField', [], {}),
            'plate_type': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'plated_activity_id': ('django.db.models.fields.IntegerField', [], {'unique': 'True', 'null': 'True', 'blank': 'True'}),
            'primary_well_mg_ml_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '5', 'decimal_places': '3', 'blank': 'True'}),
            'primary_well_molar_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '13', 'decimal_places': '12', 'blank': 'True'}),
            'quadrant': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'remaining_volume': ('django.db.models.fields.FloatField', [], {'null': 'True', 'blank': 'True'}),
            'retired_activity_id': ('django.db.models.fields.IntegerField', [], {'unique': 'True', 'null': 'True', 'blank': 'True'}),
            'screening_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'status': ('django.db.models.fields.TextField', [], {}),
            'stock_plate_number': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {}),
            'well_volume': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '9', 'blank': 'True'})
        },
        u'db.platelocation': {
            'Meta': {'object_name': 'PlateLocation', 'db_table': "u'plate_location'"},
            'bin': ('django.db.models.fields.TextField', [], {}),
            'freezer': ('django.db.models.fields.TextField', [], {}),
            'plate_location_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'room': ('django.db.models.fields.TextField', [], {}),
            'shelf': ('django.db.models.fields.TextField', [], {})
        },
        u'db.plateupdateactivity': {
            'Meta': {'object_name': 'PlateUpdateActivity', 'db_table': "u'plate_update_activity'"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'plate_id': ('django.db.models.fields.IntegerField', [], {}),
            'update_activity_id': ('django.db.models.fields.IntegerField', [], {'unique': 'True'})
        },
        u'db.publication': {
            'Meta': {'object_name': 'Publication', 'db_table': "u'publication'"},
            'attached_file': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AttachedFile']", 'unique': 'True', 'null': 'True', 'blank': 'True'}),
            'authors': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'journal': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'pages': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'publication_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'pubmed_central_id': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'pubmed_id': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'title': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {}),
            'volume': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'year_published': ('django.db.models.fields.TextField', [], {'blank': 'True'})
        },
        u'db.reagent': {
            'Meta': {'object_name': 'Reagent', 'db_table': "u'reagent'"},
            'library_contents_version': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.LibraryContentsVersion']", 'null': 'True'}),
            'reagent_id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'substance_id': ('django.db.models.fields.CharField', [], {'default': "'UWPRH326'", 'unique': 'True', 'max_length': '8'}),
            'vendor_batch_id': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'vendor_identifier': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'vendor_name': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'well': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Well']", 'null': 'True'})
        },
        u'db.reagentpublicationlink': {
            'Meta': {'object_name': 'ReagentPublicationLink', 'db_table': "u'reagent_publication_link'"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'publication_id': ('django.db.models.fields.IntegerField', [], {'unique': 'True'}),
            'reagent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Reagent']"})
        },
        u'db.resultvalue': {
            'Meta': {'object_name': 'ResultValue', 'db_table': "u'result_value'"},
            'assay_well_control_type': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'data_column': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.DataColumn']", 'null': 'True', 'blank': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'is_exclude': ('django.db.models.fields.NullBooleanField', [], {'null': 'True', 'blank': 'True'}),
            'is_positive': ('django.db.models.fields.NullBooleanField', [], {'null': 'True', 'blank': 'True'}),
            'numeric_value': ('django.db.models.fields.FloatField', [], {'null': 'True', 'blank': 'True'}),
            'result_value_id': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'value': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'well': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Well']", 'null': 'True', 'blank': 'True'})
        },
        u'db.rnaicherrypickrequest': {
            'Meta': {'object_name': 'RnaiCherryPickRequest', 'db_table': "u'rnai_cherry_pick_request'"},
            'cherry_pick_request': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickRequest']", 'primary_key': 'True'})
        },
        u'db.schemahistory': {
            'Meta': {'object_name': 'SchemaHistory', 'db_table': "u'schema_history'"},
            'comment': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'date_updated': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'screensaver_revision': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'})
        },
        u'db.screen': {
            'Meta': {'object_name': 'Screen', 'db_table': "u'screen'"},
            'abase_protocol_id': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'abase_study_id': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'amount_to_be_charged_for_screen': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '9', 'decimal_places': '2', 'blank': 'True'}),
            'assay_plates_screened_count': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'assay_type': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'billing_comments': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'billing_info_return_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'comments': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'coms_approval_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'coms_registration_number': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'created_by': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']", 'null': 'True', 'blank': 'True'}),
            'data_meeting_complete': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'data_meeting_scheduled': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'data_privacy_expiration_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'data_privacy_expiration_notified_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'data_sharing_level': ('django.db.models.fields.IntegerField', [], {}),
            'date_charged': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'date_completed5kcompounds': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'date_created': ('django.db.models.fields.DateTimeField', [], {}),
            'date_faxed_to_billing_department': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'date_loaded': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_of_application': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'date_publicly_available': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'facilities_and_administration_charge': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '9', 'decimal_places': '2', 'blank': 'True'}),
            'facility_id': ('django.db.models.fields.TextField', [], {'unique': 'True'}),
            'fee_form_requested_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'fee_form_requested_initials': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'image_url': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'is_billing_for_supplies_only': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'is_fee_form_on_file': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'lab_head': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.LabHead']", 'null': 'True', 'blank': 'True'}),
            'lead_screener': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreeningRoomUser']", 'null': 'True', 'blank': 'True'}),
            'libraries_screened_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'library_plates_data_analyzed_count': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'library_plates_data_loaded_count': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'library_plates_screened_count': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'max_allowed_data_privacy_expiration_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'max_data_loaded_replicate_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'max_screened_replicate_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'min_allowed_data_privacy_expiration_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'min_data_loaded_replicate_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'min_screened_replicate_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'perturbagen_molar_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '13', 'decimal_places': '12', 'blank': 'True'}),
            'perturbagen_ug_ml_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '5', 'decimal_places': '3', 'blank': 'True'}),
            'pin_transfer_admin_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']", 'null': 'True', 'blank': 'True'}),
            'project_id': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'project_phase': ('django.db.models.fields.TextField', [], {}),
            'pubchem_assay_id': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'pubchem_deposited_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'publishable_protocol': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'publishable_protocol_comments': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'publishable_protocol_date_entered': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'publishable_protocol_entered_by': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'screen_id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'screen_type': ('django.db.models.fields.TextField', [], {}),
            'screened_experimental_well_count': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'see_comments': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'species': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'status': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'status_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'study_type': ('django.db.models.fields.TextField', [], {}),
            'summary': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'title': ('django.db.models.fields.TextField', [], {}),
            'to_be_requested': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'total_plated_lab_cherry_picks': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'transfection_agent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.TransfectionAgent']", 'null': 'True', 'blank': 'True'}),
            'unique_screened_experimental_well_count': ('django.db.models.fields.IntegerField', [], {'default': '0'}),
            'url': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {}),
            'well_studied': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Well']", 'null': 'True', 'blank': 'True'})
        },
        u'db.screenbillingitem': {
            'Meta': {'object_name': 'ScreenBillingItem', 'db_table': "u'screen_billing_item'"},
            'amount': ('django.db.models.fields.DecimalField', [], {'max_digits': '9', 'decimal_places': '2'}),
            'date_sent_for_billing': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'item_to_be_charged': ('django.db.models.fields.TextField', [], {}),
            'ordinal': ('django.db.models.fields.IntegerField', [], {}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"})
        },
        u'db.screenercherrypick': {
            'Meta': {'object_name': 'ScreenerCherryPick', 'db_table': "u'screener_cherry_pick'"},
            'cherry_pick_request': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickRequest']"}),
            'screened_well': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Well']"}),
            'screener_cherry_pick_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.screenfundingsupportlink': {
            'Meta': {'object_name': 'ScreenFundingSupportLink', 'db_table': "u'screen_funding_support_link'"},
            'funding_support': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.FundingSupport']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"})
        },
        u'db.screening': {
            'Meta': {'object_name': 'Screening', 'db_table': "u'screening'"},
            'activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.LabActivity']", 'primary_key': 'True'}),
            'assay_protocol': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'assay_protocol_last_modified_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'assay_protocol_type': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'assay_well_volume': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '9', 'blank': 'True'}),
            'number_of_replicates': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'volume_transferred_per_well_to_assay_plates': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '9', 'blank': 'True'})
        },
        u'db.screeningroomuser': {
            'Meta': {'object_name': 'ScreeningRoomUser', 'db_table': "u'screening_room_user'"},
            'coms_crhba_permit_number': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'coms_crhba_permit_principal_investigator': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'lab_head': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.LabHead']", 'null': 'True', 'blank': 'True'}),
            'last_notified_rnaiua_checklist_item_event': ('django.db.models.fields.related.ForeignKey', [], {'blank': 'True', 'related_name': "u'rnai_ua_user'", 'null': 'True', 'to': u"orm['db.ChecklistItemEvent']"}),
            'last_notified_smua_checklist_item_event': ('django.db.models.fields.related.ForeignKey', [], {'blank': 'True', 'related_name': "u'smua_user'", 'null': 'True', 'to': u"orm['db.ChecklistItemEvent']"}),
            'screensaver_user': ('django.db.models.fields.related.OneToOneField', [], {'to': u"orm['db.ScreensaverUser']", 'unique': 'True', 'primary_key': 'True'}),
            'user_classification': ('django.db.models.fields.TextField', [], {})
        },
        u'db.screeningroomuserfacilityusagerole': {
            'Meta': {'object_name': 'ScreeningRoomUserFacilityUsageRole', 'db_table': "u'screening_room_user_facility_usage_role'"},
            'facility_usage_role': ('django.db.models.fields.TextField', [], {}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'screening_room_user': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreeningRoomUser']"})
        },
        u'db.screenkeyword': {
            'Meta': {'object_name': 'ScreenKeyword', 'db_table': "u'screen_keyword'"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'keyword': ('django.db.models.fields.TextField', [], {}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"})
        },
        u'db.screenpublicationlink': {
            'Meta': {'object_name': 'ScreenPublicationLink', 'db_table': "u'screen_publication_link'"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'publication_id': ('django.db.models.fields.IntegerField', [], {'unique': 'True'}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"})
        },
        u'db.screenresult': {
            'Meta': {'object_name': 'ScreenResult', 'db_table': "u'screen_result'"},
            'channel_count': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'created_by': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']", 'null': 'True', 'blank': 'True'}),
            'date_created': ('django.db.models.fields.DateTimeField', [], {}),
            'date_loaded': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_publicly_available': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'experimental_well_count': ('django.db.models.fields.IntegerField', [], {}),
            'replicate_count': ('django.db.models.fields.IntegerField', [], {}),
            'screen': ('django.db.models.fields.related.OneToOneField', [], {'to': u"orm['db.Screen']", 'unique': 'True'}),
            'screen_result_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.screenresultupdateactivity': {
            'Meta': {'object_name': 'ScreenResultUpdateActivity', 'db_table': "u'screen_result_update_activity'"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'screen_result': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreenResult']"}),
            'update_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']"})
        },
        u'db.screensaveruser': {
            'Meta': {'object_name': 'ScreensaverUser', 'db_table': "u'screensaver_user'"},
            'comments': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'created_by': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']", 'null': 'True', 'blank': 'True'}),
            'date_created': ('django.db.models.fields.DateTimeField', [], {'default': 'datetime.datetime.now'}),
            'date_loaded': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'date_publicly_available': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'digested_password': ('django.db.models.fields.TextField', [], {'null': 'True'}),
            'ecommons_id': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'email': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'first_name': ('django.db.models.fields.TextField', [], {}),
            'harvard_id': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'harvard_id_expiration_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'harvard_id_requested_expiration_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'last_name': ('django.db.models.fields.TextField', [], {}),
            'login_id': ('django.db.models.fields.TextField', [], {'unique': 'True', 'null': 'True'}),
            'mailing_address': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'phone': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'screensaver_user_id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'user': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['reports.UserProfile']", 'null': 'True', 'on_delete': 'models.SET_NULL'}),
            'username': ('django.db.models.fields.TextField', [], {'unique': 'True', 'null': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {'default': '1', 'blank': 'True'})
        },
        u'db.screensaveruserrole': {
            'Meta': {'object_name': 'ScreensaverUserRole', 'db_table': "u'screensaver_user_role'"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'screensaver_user': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']"}),
            'screensaver_user_role': ('django.db.models.fields.TextField', [], {})
        },
        u'db.screensaveruserupdateactivity': {
            'Meta': {'object_name': 'ScreensaverUserUpdateActivity', 'db_table': "u'screensaver_user_update_activity'"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'screensaver_user': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']"}),
            'update_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']"})
        },
        u'db.screenstatusitem': {
            'Meta': {'object_name': 'ScreenStatusItem', 'db_table': "u'screen_status_item'", 'index_together': "((u'screen', u'status', u'status_date'),)"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"}),
            'status': ('django.db.models.fields.TextField', [], {}),
            'status_date': ('django.db.models.fields.DateField', [], {})
        },
        u'db.screenupdateactivity': {
            'Meta': {'object_name': 'ScreenUpdateActivity', 'db_table': "u'screen_update_activity'"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"}),
            'update_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']"})
        },
        u'db.serviceactivity': {
            'Meta': {'object_name': 'ServiceActivity', 'db_table': "u'service_activity'"},
            'activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Activity']", 'primary_key': 'True'}),
            'service_activity_type': ('django.db.models.fields.TextField', [], {}),
            'serviced_screen': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']", 'null': 'True', 'blank': 'True'}),
            'serviced_user': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreeningRoomUser']"})
        },
        u'db.silencingreagent': {
            'Meta': {'object_name': 'SilencingReagent', 'db_table': "u'silencing_reagent'"},
            'anti_sense_sequence': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'duplex_wells': ('django.db.models.fields.related.ManyToManyField', [], {'to': u"orm['db.Well']", 'symmetrical': 'False'}),
            'facility_gene': ('django.db.models.fields.related.ForeignKey', [], {'blank': 'True', 'related_name': "u'facility_reagent'", 'unique': 'True', 'null': 'True', 'to': u"orm['db.Gene']"}),
            'is_restricted_sequence': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'reagent': ('django.db.models.fields.related.OneToOneField', [], {'to': u"orm['db.Reagent']", 'unique': 'True', 'primary_key': 'True'}),
            'sequence': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'silencing_reagent_type': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'vendor_gene': ('django.db.models.fields.related.ForeignKey', [], {'blank': 'True', 'related_name': "u'vendor_reagent'", 'unique': 'True', 'null': 'True', 'to': u"orm['db.Gene']"})
        },
        u'db.smallmoleculechembankid': {
            'Meta': {'object_name': 'SmallMoleculeChembankId', 'db_table': "u'small_molecule_chembank_id'"},
            'chembank_id': ('django.db.models.fields.IntegerField', [], {}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'reagent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Reagent']"})
        },
        u'db.smallmoleculechemblid': {
            'Meta': {'object_name': 'SmallMoleculeChemblId', 'db_table': "u'small_molecule_chembl_id'"},
            'chembl_id': ('django.db.models.fields.IntegerField', [], {}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'reagent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Reagent']"})
        },
        u'db.smallmoleculecherrypickrequest': {
            'Meta': {'object_name': 'SmallMoleculeCherryPickRequest', 'db_table': "u'small_molecule_cherry_pick_request'"},
            'cherry_pick_request': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.CherryPickRequest']", 'primary_key': 'True'})
        },
        u'db.smallmoleculecompoundname': {
            'Meta': {'object_name': 'SmallMoleculeCompoundName', 'db_table': "u'small_molecule_compound_name'"},
            'compound_name': ('django.db.models.fields.TextField', [], {}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'ordinal': ('django.db.models.fields.IntegerField', [], {}),
            'reagent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Reagent']"})
        },
        u'db.smallmoleculepubchemcid': {
            'Meta': {'object_name': 'SmallMoleculePubchemCid', 'db_table': "u'small_molecule_pubchem_cid'"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'pubchem_cid': ('django.db.models.fields.IntegerField', [], {}),
            'reagent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Reagent']"})
        },
        u'db.smallmoleculereagent': {
            'Meta': {'object_name': 'SmallMoleculeReagent', 'db_table': "u'small_molecule_reagent'"},
            'inchi': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'is_restricted_structure': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'molecular_formula': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'molecular_mass': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '15', 'decimal_places': '9', 'blank': 'True'}),
            'molecular_weight': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '15', 'decimal_places': '9', 'blank': 'True'}),
            'reagent': ('django.db.models.fields.related.OneToOneField', [], {'to': u"orm['db.Reagent']", 'unique': 'True', 'primary_key': 'True'}),
            'smiles': ('django.db.models.fields.TextField', [], {'blank': 'True'})
        },
        u'db.studyreagentlink': {
            'Meta': {'object_name': 'StudyReagentLink', 'db_table': "u'study_reagent_link'"},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'reagent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Reagent']"}),
            'study': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Screen']"})
        },
        u'db.substance': {
            'Meta': {'object_name': 'Substance'},
            'comment': ('django.db.models.fields.TextField', [], {}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'})
        },
        u'db.transfectionagent': {
            'Meta': {'object_name': 'TransfectionAgent', 'db_table': "u'transfection_agent'"},
            'transfection_agent_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'value': ('django.db.models.fields.TextField', [], {'unique': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {})
        },
        u'db.userchecklistitem': {
            'Meta': {'object_name': 'UserChecklistItem', 'db_table': "u'user_checklist_item'"},
            'admin_user': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "u'userchecklistitems_created'", 'to': u"orm['db.ScreensaverUser']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'item_group': ('django.db.models.fields.TextField', [], {}),
            'item_name': ('django.db.models.fields.TextField', [], {}),
            'screensaver_user': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.ScreensaverUser']"}),
            'status': ('django.db.models.fields.TextField', [], {}),
            'status_date': ('django.db.models.fields.DateField', [], {})
        },
        u'db.well': {
            'Meta': {'object_name': 'Well', 'db_table': "u'well'"},
            'barcode': ('django.db.models.fields.TextField', [], {'unique': 'True', 'null': 'True'}),
            'deprecation_admin_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']", 'null': 'True', 'blank': 'True'}),
            'facility_id': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'is_deprecated': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'latest_released_reagent': ('django.db.models.fields.related.ForeignKey', [], {'blank': 'True', 'related_name': "u'reagent_well'", 'null': 'True', 'to': u"orm['db.Reagent']"}),
            'library': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Library']"}),
            'library_well_type': ('django.db.models.fields.TextField', [], {}),
            'mg_ml_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '5', 'decimal_places': '3', 'blank': 'True'}),
            'molar_concentration': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '13', 'decimal_places': '12', 'blank': 'True'}),
            'plate_number': ('django.db.models.fields.IntegerField', [], {}),
            'version': ('django.db.models.fields.IntegerField', [], {'null': 'True', 'blank': 'True'}),
            'well_id': ('django.db.models.fields.TextField', [], {'primary_key': 'True'}),
            'well_name': ('django.db.models.fields.TextField', [], {})
        },
        u'db.wellvolumeadjustment': {
            'Meta': {'object_name': 'WellVolumeAdjustment', 'db_table': "u'well_volume_adjustment'"},
            'copy': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Copy']"}),
            'lab_cherry_pick': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.LabCherryPick']", 'null': 'True', 'blank': 'True'}),
            'version': ('django.db.models.fields.IntegerField', [], {}),
            'volume': ('django.db.models.fields.DecimalField', [], {'null': 'True', 'max_digits': '10', 'decimal_places': '9', 'blank': 'True'}),
            'well': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.Well']"}),
            'well_volume_adjustment_id': ('django.db.models.fields.IntegerField', [], {'primary_key': 'True'}),
            'well_volume_correction_activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.WellVolumeCorrectionActivity']", 'null': 'True', 'blank': 'True'})
        },
        u'db.wellvolumecorrectionactivity': {
            'Meta': {'object_name': 'WellVolumeCorrectionActivity', 'db_table': "u'well_volume_correction_activity'"},
            'activity': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['db.AdministrativeActivity']", 'primary_key': 'True'})
        },
        u'reports.apilog': {
            'Meta': {'unique_together': "(('ref_resource_name', 'key', 'date_time'),)", 'object_name': 'ApiLog'},
            'added_keys': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'api_action': ('django.db.models.fields.CharField', [], {'max_length': '10'}),
            'comment': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'date_time': ('django.db.models.fields.DateTimeField', [], {}),
            'diff_keys': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'diffs': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'json_field': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'key': ('django.db.models.fields.CharField', [], {'max_length': '128'}),
            'parent_log': ('django.db.models.fields.related.ForeignKey', [], {'related_name': "'child_logs'", 'null': 'True', 'to': u"orm['reports.ApiLog']"}),
            'ref_resource_name': ('django.db.models.fields.CharField', [], {'max_length': '128'}),
            'removed_keys': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'uri': ('django.db.models.fields.TextField', [], {}),
            'user_id': ('django.db.models.fields.IntegerField', [], {}),
            'username': ('django.db.models.fields.CharField', [], {'max_length': '128'})
        },
        u'reports.job': {
            'Meta': {'object_name': 'Job'},
            'comment': ('django.db.models.fields.TextField', [], {'null': 'True'}),
            'date_time_fullfilled': ('django.db.models.fields.DateTimeField', [], {'null': 'True'}),
            'date_time_processing': ('django.db.models.fields.DateTimeField', [], {'null': 'True'}),
            'date_time_requested': ('django.db.models.fields.DateTimeField', [], {'default': 'datetime.datetime.now'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'input_filename': ('django.db.models.fields.TextField', [], {'null': 'True'}),
            'path_info': ('django.db.models.fields.TextField', [], {'null': 'True'}),
            'remote_addr': ('django.db.models.fields.TextField', [], {'null': 'True'}),
            'request_method': ('django.db.models.fields.TextField', [], {'null': 'True'}),
            'response_code': ('django.db.models.fields.IntegerField', [], {}),
            'response_content': ('django.db.models.fields.TextField', [], {'null': 'True'}),
            'response_filename': ('django.db.models.fields.TextField', [], {'null': 'True'})
        },
        u'reports.listlog': {
            'Meta': {'unique_together': "(('apilog', 'ref_resource_name', 'key', 'uri'),)", 'object_name': 'ListLog'},
            'apilog': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['reports.ApiLog']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'key': ('django.db.models.fields.CharField', [], {'max_length': '128'}),
            'ref_resource_name': ('django.db.models.fields.CharField', [], {'max_length': '64'}),
            'uri': ('django.db.models.fields.TextField', [], {})
        },
        u'reports.metahash': {
            'Meta': {'unique_together': "(('scope', 'key'),)", 'object_name': 'MetaHash'},
            'alias': ('django.db.models.fields.CharField', [], {'max_length': '64', 'blank': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'json_field': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'json_field_type': ('django.db.models.fields.CharField', [], {'max_length': '128', 'null': 'True', 'blank': 'True'}),
            'key': ('django.db.models.fields.CharField', [], {'max_length': '64', 'blank': 'True'}),
            'linked_field_type': ('django.db.models.fields.CharField', [], {'max_length': '128', 'null': 'True', 'blank': 'True'}),
            'ordinal': ('django.db.models.fields.IntegerField', [], {}),
            'scope': ('django.db.models.fields.CharField', [], {'max_length': '64', 'blank': 'True'})
        },
        u'reports.permission': {
            'Meta': {'unique_together': "(('scope', 'key', 'type'),)", 'object_name': 'Permission'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'key': ('django.db.models.fields.CharField', [], {'max_length': '64', 'blank': 'True'}),
            'scope': ('django.db.models.fields.CharField', [], {'max_length': '64', 'blank': 'True'}),
            'type': ('django.db.models.fields.CharField', [], {'max_length': '35'})
        },
        u'reports.record': {
            'Meta': {'object_name': 'Record'},
            'base_value1': ('django.db.models.fields.TextField', [], {}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'scope': ('django.db.models.fields.CharField', [], {'max_length': '64', 'blank': 'True'})
        },
        u'reports.recordmultivalue': {
            'Meta': {'unique_together': "(('field_meta', 'parent', 'ordinal'),)", 'object_name': 'RecordMultiValue'},
            'field_meta': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['reports.MetaHash']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'ordinal': ('django.db.models.fields.IntegerField', [], {}),
            'parent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['reports.Record']"}),
            'value': ('django.db.models.fields.TextField', [], {})
        },
        u'reports.recordvalue': {
            'Meta': {'object_name': 'RecordValue'},
            'field_meta': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['reports.MetaHash']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'parent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['reports.Record']"}),
            'value': ('django.db.models.fields.TextField', [], {'null': 'True'})
        },
        u'reports.recordvaluecomplex': {
            'Meta': {'object_name': 'RecordValueComplex'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'parent': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['reports.Record']", 'unique': 'True'}),
            'value1': ('django.db.models.fields.TextField', [], {'null': 'True'}),
            'value2': ('django.db.models.fields.TextField', [], {'null': 'True'})
        },
        u'reports.usergroup': {
            'Meta': {'object_name': 'UserGroup'},
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'name': ('django.db.models.fields.TextField', [], {'unique': 'True'}),
            'permissions': ('django.db.models.fields.related.ManyToManyField', [], {'to': u"orm['reports.Permission']", 'symmetrical': 'False'}),
            'super_groups': ('django.db.models.fields.related.ManyToManyField', [], {'related_name': "'sub_groups'", 'symmetrical': 'False', 'to': u"orm['reports.UserGroup']"}),
            'users': ('django.db.models.fields.related.ManyToManyField', [], {'to': u"orm['reports.UserProfile']", 'symmetrical': 'False'})
        },
        u'reports.userprofile': {
            'Meta': {'object_name': 'UserProfile'},
            'comments': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'created_by_username': ('django.db.models.fields.TextField', [], {'null': 'True'}),
            'ecommons_id': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'email': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'gender': ('django.db.models.fields.CharField', [], {'max_length': '15', 'null': 'True'}),
            'harvard_id': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'harvard_id_expiration_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            'harvard_id_requested_expiration_date': ('django.db.models.fields.DateField', [], {'null': 'True', 'blank': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'json_field': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'json_field_type': ('django.db.models.fields.CharField', [], {'max_length': '128', 'null': 'True', 'blank': 'True'}),
            'mailing_address': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'permissions': ('django.db.models.fields.related.ManyToManyField', [], {'to': u"orm['reports.Permission']", 'symmetrical': 'False'}),
            'phone': ('django.db.models.fields.TextField', [], {'null': 'True', 'blank': 'True'}),
            'user': ('django.db.models.fields.related.OneToOneField', [], {'to': u"orm['auth.User']", 'unique': 'True'}),
            'username': ('django.db.models.fields.TextField', [], {'unique': 'True'})
        },
        u'reports.vocabularies': {
            'Meta': {'unique_together': "(('scope', 'key'),)", 'object_name': 'Vocabularies'},
            'alias': ('django.db.models.fields.CharField', [], {'max_length': '64', 'blank': 'True'}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'json_field': ('django.db.models.fields.TextField', [], {'blank': 'True'}),
            'key': ('django.db.models.fields.CharField', [], {'max_length': '128', 'blank': 'True'}),
            'ordinal': ('django.db.models.fields.IntegerField', [], {}),
            'scope': ('django.db.models.fields.CharField', [], {'max_length': '128', 'blank': 'True'}),
            'title': ('django.db.models.fields.CharField', [], {'max_length': '512', 'blank': 'True'})
        }
    }

    complete_apps = ['db']
    symmetrical = True