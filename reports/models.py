from __future__ import unicode_literals

from collections import OrderedDict
import json
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db import transaction
from tastypie.utils.dict import dict_strip_unicode_keys
from aldjemy.core import get_engine


logger = logging.getLogger(__name__)


class MetaManager(models.Manager):

    def __init__(self, **kwargs):
        super(MetaManager,self).__init__(**kwargs)

    def get_or_none(self, function=None, **kwargs):
        try:
            x = self.get(**kwargs)
            if x and function:
                return function(x)
            else:
                return x
        except self.model.DoesNotExist: 
            return None

    def get_and_parse(self, scope='', field_definition_scope='fields.field', 
                      clear=False):
        '''
        @param scope - i.e. the table to get field definitions for
        @param field_definition_scope - where the field properties are defined
        @param clear to clear the cache

        Query the metahash table for data identified by "scope", and fields 
        defined by "field_definition_scope"
            e.g. "fields.screensaveruser", or "fields.screen"
        field_definition_scope - also defines what is in the the json_field for
        this hash;
            e.g. "fields.field", or "fields.resource, or fields.vocabulary"
        '''
        metahash = {}
        if not clear:
            metahash = cache.get('metahash:'+scope)
        else:
            cache.delete('metahash:'+scope)
            
        if not metahash:
            metahash = self._get_and_parse(
                scope=scope, field_definition_scope=field_definition_scope)
            cache.set('metahash:'+scope, metahash);
            logger.debug(
                'get_and_parse done, for %r, hash found: %r', 
                scope, metahash.keys())
        else:
            logger.debug('retrieve the cached field definitions for %r',scope)
        return metahash


    def _get_and_parse(self, scope='', 
                          field_definition_scope='fields.field'):
        '''
        non-cached Query the metahash table for data identified by "scope", 
        and fields defined by "field_definition_scope"
            e.g. "fields.screensaveruser", or "fields.screen"
        field_definition_scope - also defines what is in the the json_field for 
        this hash;
            e.g. "fields.field", or "fields.resource, or fields.vocabulary"
        '''
        logger.debug(
            'get_and_parse table field definitions for scope: %r, fds: %r',
            scope, field_definition_scope)
        # try to make clear that the field definitions, though stored in the 
        # metahash as well, could be in a separate table
        field_definition_table = MetaHash.objects.all().filter(
            scope=field_definition_scope)
        if not field_definition_table:
            logger.warn('field definitions not found for: %r',
                field_definition_scope)
            return {}
        logger.debug('field_definition_table: %r', field_definition_table)
        # the objects themselves are stored in the metahash table as well
        unparsed_objects = \
            MetaHash.objects.all().filter(scope=scope).order_by('ordinal')
        logger.debug('unparsed_objects: %r', unparsed_objects)
        parsed_objects = OrderedDict()
        for unparsed_object in unparsed_objects:
            parsed_object = {}
            # only need the key from the field definition table
            for field_key in [ x.key for x in field_definition_table]:  
                parsed_object.update(
                    { field_key : unparsed_object.get_field(field_key) })
            
            # NOTE: choices for the "vocabulary_scope_ref" are being stored 
            # here for convenience
            
            if parsed_object.get(u'vocabulary_scope_ref'):
                vocab_ref = parsed_object['vocabulary_scope_ref']
                parsed_object['choices'] = [
                    x.key for x in Vocabulary.objects.all().filter(
                        scope=vocab_ref)]
            
            parsed_objects[unparsed_object.key] = \
                dict_strip_unicode_keys(parsed_object)

        return parsed_objects


class LogDiff(models.Model):
    
    # reference to the parent ApiLog, note that Django emulates the CASCADE delete
    log = models.ForeignKey('ApiLog', on_delete=models.CASCADE)
    
    #field = models.ForeignKey('Metahash')
    field_key = models.TextField()
    field_scope = models.TextField()
    
    before = models.TextField(null=True)
    after = models.TextField(null=True)
    
    class Meta:
        unique_together = (('log','field_key','field_scope'))    

    def __repr__(self):
        return (
            "<LogDiff(ref_resource_name=%r, key=%r, field=%r)>" 
            % (self.log.ref_resource_name, self.log.key, self.field_key))

API_ACTION_POST = 'POST'
API_ACTION_PUT = 'PUT'
# NOTE: "CREATE" - to distinguish PATCH/modify, PATCH/create
API_ACTION_CREATE = 'CREATE' 
API_ACTION_PATCH = 'PATCH'
API_ACTION_DELETE = 'DELETE'
API_ACTION_CHOICES = ((API_ACTION_POST,API_ACTION_POST),
                      (API_ACTION_PUT,API_ACTION_PUT),
                      (API_ACTION_CREATE,API_ACTION_CREATE),
                      (API_ACTION_PATCH,API_ACTION_PATCH),
                      (API_ACTION_DELETE,API_ACTION_DELETE))
class ApiLog(models.Model):
    
    objects = models.Manager()
    
    # FIXME: change to foreign key
    user_id = models.IntegerField(null=False)
    username = models.CharField(null=False, max_length=128)

    # Name of the resource, i.e. "apilog" or "screen", "user", etc.
    ref_resource_name = models.CharField(
        null=False, max_length=128, db_index=True)

    # Full public key of the resource instance being logged (may be composite, 
    # separted by '/')
    key = models.CharField(null=False, max_length=128, db_index=True)
    
    # Full uri of the resource instance being logged, 
    # a combination of [base api uri]/[resource_name]/[key]
    uri = models.TextField(null=False)
    
    # Date and time of the update; this is the key for the apilog record
    date_time = models.DateTimeField(null=False)
    
    api_action = models.CharField(
        max_length=10, null=False, choices=API_ACTION_CHOICES)
    
    comment = models.TextField(null=True)
    
    parent_log = models.ForeignKey('self', related_name='child_logs', null=True)
    
    # Nested fields are defined in the json_field
    json_field = models.TextField(null=True)
    
    class Meta:
        unique_together = (('ref_resource_name', 'key', 'date_time'))    

    def __init__(self, *args, **kwargs):
        self.diffs = {}
        models.Model.__init__(self, *args, **kwargs)
    
    def __repr__(self):
        return (
            '<ApiLog(id=%d, api_action=%r, ref_resource_name=%r, '
            'key=%r, date_time=%r)>'
            % (self.id, self.api_action, self.ref_resource_name, self.key,
               self.date_time))

    @staticmethod   
    def json_dumps(obj):
        return json.dumps(
            obj, skipkeys=False,check_circular=True, allow_nan=True, 
            default=lambda x: str(x), cls=DjangoJSONEncoder)
    
    def save(self, **kwargs):
        ''' override to convert json fields '''
        
        # ONLY on first save, create the associated logdiffs
        is_new = self.pk is None
        models.Model.save(self, **kwargs)
        
        if is_new:
            bulk_create_diffs = []
            for key,diffs in self.diffs.items():
                assert isinstance(diffs, (list,tuple))
                assert len(diffs) == 2
                bulk_create_diffs.append(LogDiff(
                    log=self,
                    field_key = key,
                    field_scope = 'fields.%s' % self.ref_resource_name,
                    before=diffs[0],
                    after=diffs[1]))
            LogDiff.objects.bulk_create(bulk_create_diffs)
        else:
            # Note: this option should not be used for bulk creation
            for key,diffs in self.diffs.items():
                assert isinstance(diffs, (list,tuple))
                assert len(diffs) == 2
                found = False
                for logdiff in self.logdiff_set.all():
                    if logdiff.field_key == key:
                        logdiff.before = diffs[0]
                        logdiff.after = diffs[1]
                        logdiff.save()
                        found = True
                if not found:
                    LogDiff.objects.create(
                        log=self,
                        field_key = key,
                        field_scope = 'fields.%s' % self.ref_resource_name,
                        before=diffs[0],
                        after=diffs[1])
                        
    @staticmethod
    def bulk_create(logs):
        '''
        Utility method - bulk create/save ApiLog instances
        '''

        logger.debug('bulk create logs: %r', logs)
        with transaction.atomic():
            with get_engine().connect() as conn:
                last_id = int(
                    conn.execute(
                        'select last_value from reports_apilog_id_seq;')
                        .scalar() or 0)
                ApiLog.objects.bulk_create(logs)
                #NOTE: Before postgresql & django 1.10 only: 
                # ids must be manually created on bulk create
                for i,log in enumerate(logs):
                    log.id = last_id+i+1
            
                bulk_create_diffs = []
                for i,log in enumerate(logs):
                    for key, logdiffs in log.diffs.items():
                        bulk_create_diffs.append(
                            LogDiff(
                                log=log,
                                field_key = key,
                                field_scope = 'fields.%s' % log.ref_resource_name,
                                before=logdiffs[0],
                                after=logdiffs[1])
                        )
                LogDiff.objects.bulk_create(bulk_create_diffs)
            
            return logs
    
class MetaHash(models.Model):
    
    objects = MetaManager()
    
    scope = models.CharField(max_length=64)
    key = models.CharField(max_length=64)
    alias = models.CharField(max_length=64)
    ordinal = models.IntegerField();

    # Required if the record represents a JSON field; 
    # choices are from the TastyPie field types
    json_field_type = models.CharField(max_length=128, null=True); 
    
    # This is the "meta" field, it contains "virtual" json fields
    json_field = models.TextField(null=True) 

    # Required if the record represents a linked field; 
    # choices are from the TastyPie field types
    linked_field_type = models.CharField(
        max_length=128, null=True); 
    
    loaded_field = None
    
    class Meta:
        unique_together = (('scope', 'key'))    
    
    def get_field_hash(self):
        if self.json_field:
            if not self.loaded_field: 
                self.loaded_field = json.loads(self.json_field)
            return self.loaded_field
        else:
            return {}
    
    def get_field(self, field):
        if field in self._meta.get_all_field_names():
            return getattr(self,field)
        temp = self.get_field_hash()
        if(field in temp):
            return temp[field]
        else:
            logger.debug('unknown field: ' + field + ' for ' + str(self))
            return None
    
    def set_field(self, field, value):
        temp = self.get_field_hash()
        temp[field] = value;
        self.json_field = json.dumps(temp)
    
    def is_json(self):
        """
        Determines if this Meta record references a JSON nested field or not
        """
        # return ( True if ( 
        # self.json_field_type and self.json_field_type.upper() != 'VIRTUAL' ) 
        # else False )
        return True if self.json_field_type else False
                
    def model_to_dict(self, scope):
        '''
        Specialized model_to_dict for JSON containing tables defined using the 
        Metahash Manager.
        @param scope for finding field definitions in the metahash table.
        e.g. "fields.<model_name>" 
        '''
        fields = MetaHash.objects.get_and_parse(scope=scope)
        dict = {}
        for key in fields.keys():
            dict[key] = self.get_field(key)
        return dict
    
    def __repr__(self):
        return (
            '<MetaHash(id=%d, scope=%r, key=%r)>'
            % (self.id, self.scope, self.key))

class Vocabulary(models.Model):
    
    objects                 = MetaManager()
    
    scope                   = models.CharField(max_length=128)
    key                     = models.CharField(max_length=128)
    alias                   = models.CharField(max_length=64)
    ordinal                 = models.IntegerField();
    title                   = models.CharField(max_length=512)
    
    # All other fields are "virtual" JSON stored fields, (unless we decide to 
    # migrate them out to real fields for rel db use cases)
    # NOTE: "json_type" for all virtual JSON fields in the entire database are
    # defined in the MetaHash
    json_field                   = models.TextField(null=True)
       
    class Meta:
        unique_together = (('scope', 'key'))    
    
    def get_field_hash(self):
        if self.json_field:
            return json.loads(self.json_field)
        else:
            return {}
    
    def get_field(self, field):
        temp = self.get_field_hash()
        if(field in temp):
            return temp[field]
        else:
            # Note, json_field is sparse, not padded with empty attributes
            logger.debug('%r, field not found: %r',self, field) 
            return None
            
    def set_field(self, field, value):
        temp = self.get_field_hash()
        temp[field] = value;
        self.json_field = json.dumps(temp)
    
    def __repr__(self):
        return (
            '<Vocabulary(scope=%r, key=%r, ordinal=%r)>'
            % (self.scope, self.key, self.ordinal))

        
class Permission(models.Model):

    scope = models.CharField(max_length=64) # scope of the permission
    key = models.CharField(max_length=64)  # key of the permission
    type = models.CharField(max_length=35)
    
    class Meta:
        unique_together = (('scope', 'key', 'type'))    
        
    def __repr__(self):
        return (
            '<Permission(scope=%r, key=%r, type=%r)>'
            % (self.scope, self.key, self.type))
   
    
class UserGroup(models.Model):
    
    name = models.TextField(unique=True)
    title = models.TextField(unique=True, null=True)
    users = models.ManyToManyField('reports.UserProfile')
    permissions = models.ManyToManyField('reports.Permission')
    
    # Super Groups: 
    # - inherit permissions from super_groups, this group is a sub_group to them
    # - inherit users from sub_groups, this group is a super_group to them
    # NOTE: Creates an "adjacency-tree" here; this can be non-performant
    # for large trees - which is not expected here; and it also requires use
    # of postgres-specific "array" types and operators 
    # (see reports.api.UserGroupResource).
    # The trade-off here is in simplicity of maintenance.
    # see "SQL antipatterns" for more discussion.
    super_groups = models.ManyToManyField('self', symmetrical=False, 
        related_name='sub_groups')

    def get_all_permissions(self, sub_groups=None, **kwargs):
        '''    
        get permissions of this group, and any inherited through super_groups
        @param kwargs to filter permissions by attributes
        '''
        if not sub_groups: 
            sub_groups = set()
        sub_groups.add(self) # TODO: test recursion check
        # start with this groups permissions
        permissions = set(self.permissions.filter(**kwargs));
        
        for group in self.super_groups.all():
            if group not in sub_groups:
                permissions.update(group.get_all_permissions(
                    sub_groups=sub_groups, **kwargs))
        
        return list(permissions)
    
    def get_all_users(self, super_groups=None, **kwargs):
        '''
        get users of this group, and any from sub_groups as well
        @param kwargs to filter users by attributes
        '''
        if not super_groups:
            super_groups = set()
        super_groups.add(self)
        # start with this groups users
        users = set(self.users.filter(**kwargs));
        
        for group in self.sub_groups.all():
            if group not in super_groups:
                users.update(group.get_all_users(
                    super_groups=super_groups, **kwargs))
        
        return list(users)
        
    def __repr__(self):
        return (
            '<UserGroup(name=%r)>'
            % (self.name))

class UserProfile(models.Model):

    objects = MetaManager()
    
    # link to django.contrib.auth.models.User, note: allow null so that it
    # can be created at the same time, but not null in practice
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE) 
    
    # will mirror the auth_user.username field
    username = models.TextField(null=False, unique=True) 
    
    # Harvard specific fields
    phone = models.TextField(null=True)
    mailing_address = models.TextField(null=True)
    comments = models.TextField(null=True)

    # TODO: make this unique
    ecommons_id = models.TextField(null=True)

    harvard_id = models.TextField(null=True)
    harvard_id_expiration_date = models.DateField(null=True)
    harvard_id_requested_expiration_date = models.DateField(null=True)
    
    created_by_username = models.TextField(null=True)

    gender = models.CharField(null=True, max_length=15)    

    # permissions assigned directly to the user, as opposed to by group
    permissions = models.ManyToManyField('reports.Permission')

    # # required if the field is a JSON field; choices are from the TastyPie 
    # # field types
    # json_field_type = models.CharField(
    #     max_length=128, null=True); 
    # 
    # # This is the "meta" field, it contains "virtual" json fields
    # json_field = models.TextField(null=True)     

    def __repr__(self):
        return (
            '<UserProfile(username=%r, id=%d, auth_user=%d)>'
            % (self.username, self.id, self.user.id))
    
    # def get_field_hash(self):
    #     if self.json_field:
    #         return json.loads(self.json_field)
    #     else:
    #         return {}
    # 
    # def get_field(self, field):
    #     temp = self.get_field_hash()
    #     if(field in temp):
    #         return temp[field]
    #     else:
    #         # Note, json_field is sparse, not padded with empty attributes
    #         logger.debug(str((self,'field not found: ',field))) 
    #         return None
    #         
    # def set_field(self, field, value):
    #     temp = self.get_field_hash()
    #     temp[field] = value;
    #     self.json_field = json.dumps(temp)

    def get_all_groups(self):

        groups = set()
        for group in self.usergroup_set.all():
            groups.add(group)
            for supergroup in group.super_groups.all():
                if supergroup in groups:
                    continue
                groups.add(supergroup)
            
        return groups
    
    @property
    def email(self):
        return self.user.email  
        
    @property    
    def first_name(self):
        "Returns the person's full name."
        return self.user.first_name
    @property
    def last_name(self):
        "Returns the person's full name."
        return self.user.last_name
    

# class ListLog(models.Model):
#     '''
#     A model that holds the keys for the items created in a "put_list"
#     '''
#     
#     apilog = models.ForeignKey('ApiLog')
# 
#     # name of the resource, i.e. "apilog" or "screen", "user", etc.
#     ref_resource_name = models.CharField(max_length=64)
# 
#     # full public key of the resource instance being logged (may be composite, 
#     # separted by '/')
#     key = models.CharField(max_length=128)
# 
#     uri = models.TextField()
#     
#     class Meta:
#         # TODO: must be apilog and either of(('ref_resource_name', 'key'),'uri')
#         # -- so probably better to handle in software
#         unique_together = (('apilog', 'ref_resource_name', 'key','uri'))    
#     
#     def __unicode__(self):
#         return unicode(str((self.ref_resource_name, self.key, self.uri )))

# # 
# ## proof-of-concept: Typed Record table with virtual field support:
# # 
# # This is a particular case of the Metahash:fields/resources tables.  
# # Instead of having "virtual" fields be in the json_field, in this case they will
# # stored in the child RecordValue table
# ## There will be one "Record" or Parent table for every node in the schema graph.
# ## Each RecordTable will have a RecordValue table
# class Record(models.Model):
#     # some fields will always be better to store on the Record table.  we'll want
#     # to indicate this as well in the Metahash.  
#     base_value1 = models.TextField()
#     
#     # the scope key points to the particular type of resource represented
#     # when joining with the RecordValue table, we will get the field key we 
#     # want by finding the "fields" for this scope in the Metahash:fields table
#     scope = models.CharField(max_length=64)
#     
# class RecordValue(models.Model):
#     # name of the parent field will be stored in the meta hash
#     parent = models.ForeignKey('Record')
#     # this field links to the column definition
#     field_meta = models.ForeignKey('Metahash')
#     # name of the value field will be stored in the meta hash
#     value = models.TextField(null=True)
# 
#     
# class RecordMultiValue(models.Model):
#     # name of the parent field will be stored in the meta hash
#     parent = models.ForeignKey('Record')
#     # this field links to the column definition
#     field_meta = models.ForeignKey('Metahash')
#     # name of the value field will be stored in the meta hash
#     value = models.TextField()
#     ordinal = models.IntegerField()
# 
#     class Meta:
#         unique_together = (('field_meta', 'parent', 'ordinal'))    
#     
# class RecordValueComplex(models.Model):
#     '''
#     This class exists to model extant complex linked tables, i.e. SMR, RNAi
#     '''
#     
#     # name of the parent field will be stored in the meta hash
#     parent = models.OneToOneField('Record', unique=True)
#     # name of the value field will be stored in the meta hash
#     value1 = models.TextField(null=True)
#     value2 = models.TextField(null=True)
# 
# class Job(models.Model):
#     # POC: 20141128
#     # Version 0.1: for processing large uploaded files
#     
#     # Client information
#     remote_addr = models.TextField(null=True)
#     request_method = models.TextField(null=True)
#     
#     # original path info, used by job process to submit full job to endpoint
#     path_info = models.TextField(null=True)
#     
#     # user comment on post
#     comment = models.TextField(null=True);
#     
#     # 0.1: original POST input is saved to this file
#     input_filename = models.TextField(null=True)
#     
#     date_time_fullfilled = models.DateTimeField(null=True) 
#     date_time_processing = models.DateTimeField(null=True) 
#     date_time_requested = models.DateTimeField(null=False, default=timezone.now) 
# 
#     # if the response is large, save to a temp file
#     response_filename = models.TextField(null=True)
#     
#     # store response to field
#     response_content = models.TextField(null=True)
#     
#     # HTTP response code, saved from the endpoint job submission on completion
#     response_code = models.IntegerField();
#     
#     def __unicode__(self):
#         return unicode(str((self.id, self.path_info, self.date_time_requested)))
#     
#     
