from django.db import models
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned
from lims.models import GetOrNoneManager

import json
import logging
import re

logger = logging.getLogger(__name__)


class FieldsManager(models.Manager):
    
    fieldinformation_map = {}

    # this is how you override a Manager's base QuerySet
    def get_query_set(self):
        return super(FieldsManager, self).get_query_set()
    
    def get_table_fields(self,table):
        """
        return the FieldInformation objects for the table, or None if not defined
        """
        return self.get_query_set().filter(table=table)
    
    def get_column_fieldinformation_by_priority(self,field_or_alias,tables_by_priority):
        """
        searches for the FieldInformation using the tables in the tables_by_priority, in the order given.
        raises an ObjectDoesNotExist exception if not found in any of them.
        :param tables_by_priority: a sequence of table names.  If an empty table name is given, then
        a search through all fields is used.  This search can result in MultipleObjectsReturned exception.
        """
        if isinstance(tables_by_priority, basestring): tables_by_priority = (tables_by_priority,)
        assert isinstance(tables_by_priority, (list, tuple)), 'search_tables must be a list or tuple'
        for i,table in enumerate(tables_by_priority):
            try:
                if(table == ''):
                    return self.get_column_fieldinformation(field_or_alias)
                return self.get_column_fieldinformation(field_or_alias, table)
            except (ObjectDoesNotExist,MultipleObjectsReturned) as e:
                if( i+1 == len(tables_by_priority)): raise Exception(str((type(e), field_or_alias,tables_by_priority, e.args)))
                
    def get_column_fieldinformation(self,field_or_alias,table_or_queryset=None):
        """
        return the FieldInformation object for the column, or None if not defined
        """
        
        fi = None
        if(table_or_queryset == None):
            try:
                return self.get_query_set().get(alias=field_or_alias, table=None, queryset=None); # TODO can use get?
            except (ObjectDoesNotExist,MultipleObjectsReturned) as e:
                logger.debug(str(('No field information for the alias: ',field_or_alias,e)))
            try:
                return self.get_query_set().get(field=field_or_alias, table=None, queryset=None); # TODO can use get?
            except (ObjectDoesNotExist,MultipleObjectsReturned) as e:
                logger.debug(str(('No field information for the field: ',field_or_alias,e)))
                raise e
        else:
            try:
                fi = self.get_query_set().get(queryset=table_or_queryset, field=field_or_alias)
                return fi
            except (ObjectDoesNotExist,MultipleObjectsReturned) as e:
                logger.debug(str(('No field information for the queryset,field: ',table_or_queryset,field_or_alias, e)))
            try:
                fi = self.get_query_set().get(queryset=table_or_queryset, alias=field_or_alias)
                return fi
            except (ObjectDoesNotExist,MultipleObjectsReturned) as e:
                logger.debug(str(('No field information for the queryset,alias: ',table_or_queryset,field_or_alias, e)))
            
            try:
                return self.get_query_set().get(table=table_or_queryset, field=field_or_alias)
            except (ObjectDoesNotExist,MultipleObjectsReturned) as e:
                logger.debug(str(('No field information for the table,field: ',table_or_queryset,field_or_alias, e)))
            try:
                return self.get_query_set().get(table=table_or_queryset, alias=field_or_alias)
            except (ObjectDoesNotExist,MultipleObjectsReturned) as e:
                logger.debug(str(('No field information for the table,alias: ',table_or_queryset,field_or_alias, e)))
                raise e
        
    #TODO: link this in to the reindex process!
    def get_search_fields(self,model):
        """
        For the full text search, return the text searchable fields.
        """
        table = model._meta.module_name
        # Only text or char fields considered, must add numeric fields manually
        fields = map(lambda x: x.name, filter(lambda x: isinstance(x, models.CharField) or isinstance(x, models.TextField), tuple(model._meta.fields)))
        final_fields = []
        for fi in self.get_table_fields(table):
            if(fi.use_for_search_index and fi.field in fields):  final_fields.append(fi)
        logger.debug(str(('get_search_fields for ',model,'returns',final_fields)))
        return final_fields
# Fields:
# null=False by default; will not allow Null values on the field
# blank=False by default

class FieldInformation(models.Model):
    manager                 = FieldsManager()
    objects                 = models.Manager() # default manager
    
    table                   = models.CharField(max_length=35, blank=True)
    field                   = models.CharField(max_length=35, blank=True)
    alias                   = models.CharField(max_length=35, blank=True)
    queryset                = models.CharField(max_length=35, blank=True)
    field_name              = models.TextField(blank=True) # override the LINCS name for display
    show_in_detail          = models.BooleanField(default=False, null=False) # Note: default=False are not set at the db level, only at the Db-api level
    show_in_list            = models.BooleanField(default=False, null=False) # Note: default=False are not set at the db level, only at the Db-api level
    order                   = models.IntegerField(null=False)
    use_for_search_index    = models.BooleanField(default=False) # Note: default=False are not set at the db level, only at the Db-api level
    related_to              = models.TextField(blank=True)
    description             = models.TextField(blank=True)
    importance              = models.TextField(blank=True)
    comments                = models.TextField(blank=True)
    is_unrestricted         = models.BooleanField(default=False, null=False)
    class Meta:
        unique_together = (('table', 'field','queryset'),('field','alias'))    
    def __unicode__(self):
        return unicode(str((self.table, self.field, self.id, self.field_name)))
    
    def get_camel_case_field_name(self):
        logger.debug(str(('create a camelCase name for:', self)))
        field_name = self.field_name.strip().title()
        # TODO: convert a trailing "Id" to "ID"
        field_name = ''.join(['ID' if x.lower()=='id' else x for x in re.split(r'[_\s]+',field_name)])
        
        #field_name = re.sub(r'[_\s]+','',field_name)
        field_name = field_name[0].lower() + field_name[1:];
        #        logger.info(str(('created camel case name', field_name, 'for', self)))
        return field_name

class MetaManager(GetOrNoneManager):

    def __init__(self, **kwargs):
        super(MetaManager,self).__init__(**kwargs)
#        self.metahash = {}


    
    # this is how you override a Manager's base QuerySet
    def get_query_set(self):
        return super(MetaManager, self).get_query_set()

    def get_metahash(self, scope=''):
        '''
        Query the metahash table for field definitions for this table
        '''
#        if not self.metahash:
        # TODO: one cannot deny, a cache might do some good here    
        metahash = {}
        # first, query the metahash for fields defined for this scope
        for fieldinformation in MetaHash.objects.all().filter(scope=scope):
            logger.debug('---- meta field for scope: ' + scope + ', ' + fieldinformation.key)
            
            field_key = fieldinformation.key
#            hash = schema['fields'][field_key]
            metahash[field_key] = {}
            hash = metahash[field_key]
            for meta_record in MetaHash.objects.all().filter(scope='metahash:fields'):  # metahash:fields are defined for all reports
                hash.update({
                      meta_record.key: MetaHash.objects.get_or_none(scope=scope, key=field_key, function=lambda x : (x.get_field(meta_record.key)) )
                      })
                    
            # now check if the field uses controlled vocabulary, look that up now.  TODO: "vocabulary_scope_ref" should be a constant
            # TODO: "vocabulary_scope_ref" needs to be created by default as a metahash:field; this argues for making it a "real" field
            if hash.get(u'vocabulary_scope_ref'):
                vocab_ref = hash['vocabulary_scope_ref']
                logger.debug(str(('looking for a vocabulary', vocab_ref )))
                hash['choices'] = [x.key for x in Vocabularies.objects.all().filter(scope=vocab_ref)]
                logger.debug(str(('got', hash['choices'] )))
            
        return metahash


class MetaHash1(models.Model):
    manager                 = MetaManager()
    objects                 = models.Manager() # default manager
    
    scope                   = models.CharField(max_length=35, blank=True)
    key                     = models.CharField(max_length=35, blank=True)
    alias                   = models.CharField(max_length=35, blank=True)
    
    value                   = models.TextField(blank=True)
    
    description             = models.TextField(blank=True)
    comment                 = models.TextField(blank=True)
    labels                  = models.TextField(blank=True)
    capabilities            = models.TextField(blank=True) # i.e. 'edit', 'textsearch'
    visibilities            = models.TextField(blank=True) # i.e. 'detail', 'list', 'edit'
    readPermissions         = models.TextField(blank=True) # i.e. roles: admin, etc.
    writePermissions         = models.TextField(blank=True)
    createPermissions         = models.TextField(blank=True)
    type                    = models.CharField(max_length=35, blank=True)
    
    class Meta:
        unique_together = (('scope', 'key'),('scope','alias'))    
    def __unicode__(self):
        return unicode(str((self.scope, self.key, self.id, self.alias)))

# Notes on MetaHash Model

class MetaHash(models.Model):
    objects                 = MetaManager()
#    objects                 = models.Manager() # default manager
    
    scope                   = models.CharField(max_length=35, blank=True)
    key                     = models.CharField(max_length=35, blank=True)
    alias                   = models.CharField(max_length=35, blank=True)
    ordinal                 = models.IntegerField();
    json_field_type         = models.CharField(max_length=128, blank=True, null=True); # required if the field is a JSON field; choices are from the TastyPie field types
    
    json_field                   = models.TextField(blank=True) # This is the "meta" field, it contains "virtual" json fields
       
    class Meta:
        unique_together = (('scope', 'key'))    
    
    def get_field_hash(self):
        if self.json_field:
            return json.loads(self.json_field)
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
        return True if self.json_field_type else False
            
    def dehydrate(self, bundle):
        return bundle
    
    def __unicode__(self):
        return unicode(str((self.scope, self.key, self.id, self.alias)))
    
    
class Vocabularies(models.Model):
    objects                 = MetaManager()
#    objects                 = models.Manager() # default manager
    
    scope                   = models.CharField(max_length=35, blank=True)
    key                     = models.CharField(max_length=35, blank=True)
    alias                   = models.CharField(max_length=35, blank=True)
    ordinal                 = models.IntegerField();
    json_field_type         = models.CharField(max_length=128, blank=True, null=True); # required if the field is a JSON field; choices are from the TastyPie field types
    
    # All other fields are "virtual" JSON stored fields, (unless we decide to migrate them out to real fields for rel db use cases)
    # NOTE: "json_type" for all virtual JSON fields in the entire database are defined in the MetaHash
    json_field                   = models.TextField(blank=True)
       
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
            logger.info(str((self,'unknown field: ',field)))
            return None
            
    def set_field(self, field, value):
        temp = self.get_field_hash()
        temp[field] = value;
        self.json_field = json.dumps(temp)
    
    
    def __unicode__(self):
        return unicode(str((self.scope, self.key, self.id)))
    
    