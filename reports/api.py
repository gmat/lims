from __future__ import unicode_literals

import cStringIO
from collections import defaultdict, OrderedDict
from copy import deepcopy
import csv
import datetime
from functools import wraps
import importlib
import json
import logging
import os
import re
import sys
import traceback

import dateutil
from django.conf import settings
from django.conf.global_settings import AUTH_USER_MODEL
from django.conf.urls import url
from django.contrib.auth.models import User as DjangoUser
from django.contrib.auth.models import UserManager
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Q
from django.db.models.aggregates import Max
from django.forms.models import model_to_dict
from django.http.request import HttpRequest
from django.http.response import HttpResponse, Http404
from django.http.response import HttpResponseBase
from django.utils import timezone
from django.utils.encoding import smart_text
import six
from sqlalchemy import select, asc, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import Any
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.sql import and_, or_, not_          , operators
from sqlalchemy.sql import asc, desc, alias, Alias
from sqlalchemy.sql import func
from sqlalchemy.sql.elements import literal_column
from sqlalchemy.sql.expression import column, join, distinct
from sqlalchemy.sql.expression import nullsfirst, nullslast
from tastypie import fields 
from tastypie.authentication import BasicAuthentication, SessionAuthentication, \
    MultiAuthentication
from tastypie.authorization import Authorization, ReadOnlyAuthorization
from tastypie.bundle import Bundle
from tastypie.constants import ALL, ALL_WITH_RELATIONS
from tastypie.exceptions import NotFound, ImmediateHttpResponse, Unauthorized, \
    BadRequest
from tastypie.http import HttpForbidden, HttpNotFound, HttpNotImplemented, \
    HttpNoContent
from tastypie.resources import Resource, ModelResource
from tastypie.resources import convert_post_to_put
import tastypie.resources
from tastypie.utils.dict import dict_strip_unicode_keys
from tastypie.utils.mime import build_content_type
from tastypie.utils.timezone import make_naive
from tastypie.utils.urls import trailing_slash
from tastypie.validation import Validation

from reports import LIST_DELIMITER_SQL_ARRAY, LIST_DELIMITER_URL_PARAM, \
    HTTP_PARAM_USE_TITLES, HTTP_PARAM_USE_VOCAB, HEADER_APILOG_COMMENT
from reports import ValidationError
from reports.dump_obj import dumpObj
from reports.models import API_ACTION_CREATE
from reports.models import MetaHash, Vocabularies, ApiLog, ListLog, Permission, \
                           UserGroup, UserProfile, Record, API_ACTION_DELETE
from reports.serializers import LimsSerializer, CsvBooleanField, CSVSerializer
from reports.sqlalchemy_resource import SqlAlchemyResource, un_cache, _concat
from reports.utils.profile_decorator import profile
from operator import itemgetter


# from db.models import ScreensaverUser
logger = logging.getLogger(__name__)

URI_VERSION = 'v1'
BASE_URI = '/reports/api/' + URI_VERSION

def parse_val(value, key, data_type):
    """
    All values are read as strings from the input files, so this function 
    converts them as directed.
    TODO: validation
    """
    try:
        if ( value is None 
            or value == '' 
            or value == 'None' 
            or value == u'None' 
            or value == u'n/a'):
            if data_type == 'string': 
                return ''
            else:  
                return None
        if data_type == 'string':
            return value
        elif data_type == 'integer':
            # todo: this is a kludge, since we want an integer from values like "5.0"
            return int(float(value))
        elif data_type == 'date':
            return dateutil.parser.parse(value)
        elif data_type == 'datetime':
            return dateutil.parser.parse(value)
        elif data_type == 'boolean':
            if value is True or value is False:
                 return value
            value = str(value)
            if(value.lower() == 'true' or value.lower() == 't'): return True
            return False
        elif data_type in ['float','decimal']:
            return float(value)
        elif data_type == 'list':
            if isinstance(value, six.string_types):
                if value.strip():
                    return (value,) # convert string to list
                else:
                    return None
            return value # otherwise, better be a list
        else:
            raise Exception('unknown data type: %s: "%s"' % (key,data_type))
    except Exception, e:
        logger.exception('value not parsed %r:%r',key, value)
        raise ValidationError(key=key,msg='parse error: %r' % str(e))
#         raise            

    
class UserGroupAuthorization(Authorization):
    
    @staticmethod
    def get_authorized_resources(user, permission_type):
        userprofile = user.userprofile
        permission_types = [permission_type]
        if permission_type == 'read':
            permission_types.append('write')
        resources_user = ( userprofile.permissions.all()
            .filter(scope='resource', type__in=permission_types)
            .values_list('key', flat=True))
        resources_group = [ permission.key 
                for group in userprofile.usergroup_set.all() 
                for permission in group.get_all_permissions(
                    scope='resource', type__in=permission_types)]
        return set(resources_user) | set(resources_group)
    
    def _is_resource_authorized(self, resource_name, user, permission_type):
        
        DEBUG_AUTHORIZATION = False or logger.isEnabledFor(logging.DEBUG)
        
        if DEBUG_AUTHORIZATION:
            logger.info("_is_resource_authorized: %s, user: %s, type: %s",
                resource_name, user, permission_type)
        scope = 'resource'
        prefix = 'permission'
        uri_separator = '/'
        permission_str =  uri_separator.join([prefix,scope,resource_name,permission_type])       

        if DEBUG_AUTHORIZATION:
            logger.info('authorization query: %s, user %s, %s' 
                % (permission_str, user, user.is_superuser))
        
        if user.is_superuser:
            logger.debug('%s:%s access allowed for super user: %s' 
                % (resource_name,permission_type,user))
            return True
        
        # FIXME: 20150708 - rewrite this using the "get_detail" method for the user, and 
        # interrogating the groups therein (post refactor of TP methods)
        userprofile = user.userprofile
        permission_types = [permission_type]
        if permission_type == 'read':
            permission_types.append('write')
        query = userprofile.permissions.all().filter(
            scope=scope, key=resource_name, type__in=permission_types)
        if query.exists():
            if DEBUG_AUTHORIZATION:
                logger.info('user %s, auth query: %s, found matching user permissions %s'
                    % (user,permission_str,[str(x) for x in query]))
            logger.info('%s:%s user explicit permission for: %s' 
                % (resource_name,permission_type,user))
            return True
        
        if DEBUG_AUTHORIZATION:
            logger.info('user %s, auth query: %s, not found in user permissions %s'
                % (user,permission_str,[str(x) for x in query]))
        
        permissions_group = [ permission 
                for group in userprofile.usergroup_set.all() 
                for permission in group.get_all_permissions(
                    scope=scope, key=resource_name, type__in=permission_types)]
        if permissions_group:
            if(logger.isEnabledFor(logging.DEBUG)):
                logger.info(str(('user',user ,'auth query', permission_str,
                    'found matching usergroup permissions', permissions_group)))
            logger.info('%s:%s usergroup permission for: %s' 
                % (resource_name,permission_type,user))
            return True
        
        logger.info(str(('user',user ,'auth query', permission_str,
             'not found in group permissions', permissions_group)))
        
        # Note: the TP framework raises the "Unauthorized" error: it then 
        # translates this into the (incorrect) HttpUnauthorized (401) response
        # Instead, raise an immediate exception with the correct 403 error code
        raise ImmediateHttpResponse(response=HttpForbidden(
            'user: %s, permission: %r not found' % (user,permission_str)))
    
    def read_list(self, object_list, bundle):
        if self._is_resource_authorized(
            self.resource_meta.resource_name, bundle.request.user, 'read'):
            return object_list

    def read_detail(self, object_list, bundle):
        if self._is_resource_authorized(
            self.resource_meta.resource_name, bundle.request.user, 'read'):
            return True

    def create_list(self, object_list, bundle):
        if self._is_resource_authorized(
            self.resource_meta.resource_name, bundle.request.user, 'write'):
            return object_list

    def create_detail(self, object_list, bundle):
        if self._is_resource_authorized(
            self.resource_meta.resource_name, bundle.request.user, 'write'):
            return True

    def update_list(self, object_list, bundle):
        if self._is_resource_authorized(
            self.resource_meta.resource_name, bundle.request.user, 'write'):
            return object_list

    def update_detail(self, object_list, bundle):
        if self._is_resource_authorized(
            self.resource_meta.resource_name, bundle.request.user, 'write'):
            return True

    def delete_list(self, object_list, bundle):
        if self._is_resource_authorized(
            self.resource_meta.resource_name, bundle.request.user, 'write'):
            return object_list

    def delete_detail(self, object_list, bundle):
        if self._is_resource_authorized(
            self.resource_meta.resource_name, bundle.request.user, 'write'):
            return True

def write_authorization(_func):
    '''
    Wrapper function to verify write authorization
    ''' 
    @wraps(_func)
    def _inner(self, *args, **kwargs):
        request = args[0]
        self._meta.authorization._is_resource_authorized(
            self._meta.resource_name,request.user,'write')
        return _func(self, *args, **kwargs)

    return _inner

def read_authorization(_func):
    '''
    Wrapper function to verify read authorization
    ''' 
    @wraps(_func)
    def _inner(self, *args, **kwargs):
        request = args[0]
        self._meta.authorization._is_resource_authorized(
            self._meta.resource_name,request.user,'read')
        return _func(self, *args, **kwargs)

    return _inner


class SuperUserAuthorization(ReadOnlyAuthorization):

    def _is_resource_authorized(self, resource_name, user, permission_type):
        if user.is_superuser:
            return True
        # Note: the TP framework raises the "Unauthorized" error: it then 
        # translates this into the (incorrect) HttpUnauthorized (401) response
        # Instead, raise an immediate exception with the correct 403 error code
        # https://tools.ietf.org/html/rfc7231#section-6.5.3
        
        uri_separator = '/'
        permission_str =  uri_separator.join([resource_name,permission_type])       
        raise ImmediateHttpResponse(response=HttpForbidden(
            'user: %s, permission: %r not found' % (user,permission_str)))
        
    def delete_list(self, object_list, bundle):
        if bundle.request.user.is_superuser:
            return object_list
        raise Unauthorized("Only superuser may delete lists.")

    def delete_detail(self, object_list, bundle):
        if bundle.request.user.is_superuser:
            return object_list
        raise Unauthorized("Only superuser may delete.")
 
    def create_list(self, object_list, bundle):
        if bundle.request.user.is_superuser:
            return object_list
        raise Unauthorized("Only superuser may create lists.")

    def create_detail(self, object_list, bundle):
        if bundle.request.user.is_superuser:
            return True
        raise Unauthorized("Only superuser may create.")

    def update_list(self, object_list, bundle):
        if bundle.request.user.is_superuser:
            return object_list
        raise Unauthorized("Only superuser may update lists.")

    def update_detail(self, object_list, bundle):
        logger.info(str(('update detail authorization', bundle.request.user)))
        if bundle.request.user.is_superuser:
            return True
        raise Unauthorized("Only superuser may update.")


class IccblBaseResource(Resource):
    """
    Override tastypie.resources.Resource to replace check:
     if not isinstance(response, HttpResponse):
        return http.HttpNoContent()
    with:
     if not isinstance(response, HttpResponseBase):
        return http.HttpNoContent()
    -- this allows for use of the StreamingHttpResponse or the HttpResponse
    """

    content_types = {
                     'xls': 'application/xls',
                     'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     'csv': 'text/csv',
                     'sdf': 'chemical/x-mdl-sdfile',
                     'json': 'application/json',
                     }

    def make_log(self, request, **kwargs):
        log = ApiLog()
        log.username = request.user.username 
        log.user_id = request.user.id 
        log.date_time = timezone.now()
        log.api_action = str((request.method)).upper()
 
        # TODO: how do we feel about passing form data in the headers?
        # TODO: abstract the form field name
        if HEADER_APILOG_COMMENT in request.META:
            log.comment = request.META[HEADER_APILOG_COMMENT]
     
        if kwargs:
            for key, value in kwargs.items():
                if hasattr(log, key):
                    setattr(log, key, value)
         
        return log

    def _get_filename(self,schema, kwargs):
        filekeys = []
        if 'id_attribute' in schema:
            filekeys.extend([ str(kwargs[key]) for 
                key in schema['id_attribute'] if key in kwargs ])
        else:
            _dict = {key:val for key,val in kwargs.items() 
                if key not in [
                    'visibilities','exact_fields','api_name','resource_name',
                    'includes','order_by']}
            for i,(x,y) in enumerate(_dict.items()):
                filekeys.append(str(x))
                filekeys.append(str(y))
                if i == 10:
                    break
                 
        filekeys.insert(0,self._meta.resource_name)
        logger.info('filekeys: %r', filekeys)
        filename = '_'.join(filekeys)
        filename = re.sub(r'[\W]+','_',filename)
        logger.debug('get_filename: %r, %r' % (filename, kwargs))
        return filename
    
    def get_format(self, request):
        '''
        Return mime-type "format" set on the request, or
        use mimeparse.best_match:
        "Return the mime-type with the highest quality ('q') from list of candidates."
        - uses Resource.content_types
        '''
        format = request.GET.get('format', 'json')
        logger.debug('format %s', format)
        if format:
            if format in self.content_types:
                format = self.content_types[format]
                logger.debug('format: %r', format)
            else:
                logger.error('unknown format: %r, options: %r', format, self.content_types)
                raise ImmediateHttpResponse("unknown format: %s" % format)
        else:
            # Try to fallback on the Accepts header.
            if request.META.get('HTTP_ACCEPT', '*/*') != '*/*':
                try:
                    import mimeparse
                    format = mimeparse.best_match(
                        self.content_types.values(), request.META['HTTP_ACCEPT'])
                    if not format:
                        raise ImmediateHttpResponse("no best match format for HTTP_ACCEPT: " +
                            request.META['HTTP_ACCEPT'])
                        
                    logger.debug('format %s, HTTP_ACCEPT: %s', format, 
                        request.META['HTTP_ACCEPT'])
                except ValueError:
                    logger.error(str(('Invalid Accept header')))
                    raise ImmediateHttpResponse('Invalid Accept header')
            elif request.META.get('CONTENT_TYPE', '*/*') != '*/*':
                format = request.META.get('CONTENT_TYPE', '*/*')
        logger.debug('got format: %s', format)
        return format

    def dispatch(self, request_type, request, **kwargs):
        """
        Override tastypie.resources.Resource to replace check:
         if not isinstance(response, HttpResponse):
            return http.HttpNoContent()
        with:
         if not isinstance(response, HttpResponseBase):
            return http.HttpNoContent()
        -- this allows for use of the StreamingHttpResponse or the HttpResponse
        
        Other modifications:
        - use of the "downloadID" cookie
        """
        allowed_methods = getattr(self._meta, "%s_allowed_methods" % request_type, None)

        if 'HTTP_X_HTTP_METHOD_OVERRIDE' in request.META:
            request.method = request.META['HTTP_X_HTTP_METHOD_OVERRIDE']

        request_method = self.method_check(request, allowed=allowed_methods)
        method = getattr(self, "%s_%s" % (request_method, request_type), None)

        if method is None:
            raise ImmediateHttpResponse(response=HttpNotImplemented())

        self.is_authenticated(request)
        self.throttle_check(request)

        # All clear. Process the request.
        convert_post_to_put(request)
        response = method(request, **kwargs)

        # Add the throttled request.
        self.log_throttled_access(request)

        # If what comes back isn't a ``HttpResponse``, assume that the
        # request was accepted and that some action occurred. This also
        # prevents Django from freaking out.
        if not isinstance(response, HttpResponseBase):
            return HttpNoContent()

        
        ### Custom iccbl-lims parameter: set cookie to tell browser javascript
        ### UI that the download request is finished
        downloadID = request.GET.get('downloadID', None)
        if downloadID:
            logger.info(str(('set cookie','downloadID', downloadID )))
            response.set_cookie('downloadID', downloadID)
        else:
            logger.debug('no downloadID: %s' % request.GET )

        return response
       
    @staticmethod    
    def create_vocabulary_rowproxy_generator(field_hash):
        '''
        Create cursor row generator:
        - generator wraps a sqlalchemy.engine.ResultProxy (cursor)
        - yields a wrapper for sqlalchemy.engine.RowProxy on each iteration
        - the wrapper will return vocabulary titles for valid vocabulary values
        in each row[key] for the key columns that are vocabulary columns.
        - returns the regular row[key] value for other columns
        '''
        vocabularies = {}
        for key, field in field_hash.iteritems():
            if field.get('vocabulary_scope_ref', None):
                scope = field.get('vocabulary_scope_ref')
                vocabularies[key] = VocabulariesResource()._get_vocabularies_by_scope(scope)
        def vocabulary_rowproxy_generator(cursor):
            class Row:
                def __init__(self, row):
                    self.row = row
                def has_key(self, key):
                    return self.row.has_key(key)
                def keys(self):
                    return self.row.keys();
                def __getitem__(self, key):
                    if not row[key]:
                        return None
                    if key in vocabularies:
                        if row[key] not in vocabularies[key]:
                            logger.error(str(('----warn, unknown vocabulary', 
                                'column', key, 'value', row[key],
                                'scope', field_hash[key]['vocabulary_scope_ref'], 
                                'vocabularies defined',vocabularies[key].keys() )))
                            return self.row[key] 
                        else:
                            # logger.info(str(('getitem', key, row[key], 
                            #    vocabularies[key][row[key]]['title'])))
                            return vocabularies[key][row[key]]['title']
                    else:
                        return self.row[key]
            for row in cursor:
                yield Row(row)
        return vocabulary_rowproxy_generator


# # TODO: this class should be constructed as a Mixin, not inheritor of ModelResource
# class PostgresSortingResource(ModelResource):
# 
#     def __init__(self, **kwargs):
#         super(PostgresSortingResource,self).__init__( **kwargs)
# 
#     def apply_sorting(self, obj_list, options):
#         '''
#         Override sorting so that we can make postgres sort nulls as less than
#          everything else.
# 
#         Caveat: this will not work with joined fields unless they have an alias.  
#         This is because it creates a field like:
#         (screensaver_user_id is null) AS "screensaver_user_id_null"
#         - if this field is duplicated in two sides of a join, then it must be 
#         referenced by an alias, or as "table".screensaver_user_id, 
#         and we are not supporting table specifications in this method, so if 
#         joined fields are used, they must be referenced by alias only.
#         '''
#         
#         obj_list = super(PostgresSortingResource, self).apply_sorting(
#             obj_list, options)
#         logger.debug(str(('order_by', obj_list.query.order_by)))
#         extra_select = {}
#         non_null_fields = options.get('non_null_fields', [])
#         new_ordering = []
# 
#         for field in obj_list.query.order_by:
#             original_field = field
#             is_null_dir = '-'  # default nulls first for ascending
#             if field.startswith('-'):
#                 is_null_dir = ''
#                 field = field[1:]
#             if field in non_null_fields:
#                 continue
#             extra_select[field+"_null"]=field + ' is null'
#             new_ordering.append(is_null_dir + field+"_null")
#             new_ordering.append(original_field)
#         logger.debug(str(('extra_select', extra_select, 
#                           'new_ordering', new_ordering)))
#         obj_list = obj_list.extra(extra_select)
# 
#         obj_list.query.clear_ordering(force_empty=True)
#         obj_list.query.add_ordering(*new_ordering)
#         
#         return obj_list


# def log_obj_create(obj_create_func):
#     @transaction.atomic()
#     @wraps(obj_create_func)
#     def _inner(self, bundle, **kwargs):
#         logger.debug(str(('decorator start; log_obj_create', kwargs)))
#         if(logger.isEnabledFor(logging.DEBUG)):
#             logger.debug(str(('----log obj_create', bundle)))
# 
#         kwargs = kwargs or {}    
#         
#         # Note: "full" logs would log all of the created data in the resource;
#         # whereas "not full" only logs the creation; with this strategy, logs 
#         # must be played backwards to recreate an entity state.
#         full = False
#         
#         bundle = obj_create_func(self, bundle=bundle, **kwargs)
#         if(logger.isEnabledFor(logging.DEBUG)):
#             logger.debug(str(('object created', bundle.obj )))
#         log = ApiLog()
#         log.username = bundle.request.user.username 
#         log.user_id = bundle.request.user.id 
#         log.date_time = timezone.now()
#         log.ref_resource_name = self._meta.resource_name
#         log.api_action = str((bundle.request.method)).upper()
#         if full:
#             log.diffs = json.dumps(bundle.obj)
#             
#         # user can specify any valid, escaped json for this field
#         # FIXME: untested
#         if 'apilog_json_field' in bundle.data:
#             log.json_field = json.dumps(bundle.data['apilog_json_field'])
#             
#         log.uri = self.get_resource_uri(bundle)
#         log.key = '/'.join([str(x) for x in self.detail_uri_kwargs(bundle).values()])
# 
#         # Form data passed in the header
#         if HEADER_APILOG_COMMENT in bundle.request.META:
#             log.comment = bundle.request.META[HEADER_APILOG_COMMENT]
#             
#         if 'parent_log' in kwargs:
#             log.parent_log = kwargs.get('parent_log', None)
#         
#         log.save()
#         if(logger.isEnabledFor(logging.DEBUG)):
#             logger.debug(str(('create, api log', log)) )
# 
#         logger.debug(str(('decorator done; log_obj_create')))
#         return bundle    
#                             
#     return _inner

def is_empty_diff(difflog):
    if not difflog:
     return True
    
    empty = True;
    for key, value in difflog.items():
        if value:
            empty = False;
    return empty

def compare_dicts(dict1, dict2, excludes=['resource_uri'], full=False):
    '''
    @param full (default False) - a full compare shows added keys as well as diff keys
    
    Note: "full" logs would log all of the created data in the resource;
    whereas "not full" only logs the creation; with this strategy, logs 
    must be played backwards to recreate an entity state.
    '''
    original_keys = set(dict1.keys())-set(excludes)
    updated_keys = set(dict2.keys())-set(excludes)
    
    intersect_keys = original_keys.intersection(updated_keys)
    log = {'diffs': {}}
    
    added_keys = list(updated_keys - intersect_keys)
    if len(added_keys)>0: 
        log['added_keys'] = added_keys
        if full:
            log['diffs'].update( 
                dict(zip( added_keys,([None,dict2[key]] for key in added_keys if dict2[key]) )) )
    
    removed_keys = list(original_keys- intersect_keys)
    if len(removed_keys)>0: 
        log['removed_keys'] = removed_keys
        if full:
            log['diffs'].update(
                dict(zip(removed_keys,([dict1[key],None] for key in removed_keys if dict1[key]) )) )
    
    diff_keys = list()
    for key in intersect_keys:
        val1 = dict1[key]
        val2 = dict2[key]
        # NOTE: Tastypie converts to tz naive on serialization; then it 
        # forces it to the default tz upon deserialization (in the the 
        # DateTimeField convert method); for the purpose of this comparison,
        # then, make both naive.
        if isinstance(val2, datetime.datetime):
            val2 = make_naive(val2)
        if val1 != val2: 
            diff_keys.append(key)
    # Note, simple equality not used, since the serialization isn't 
    # symmetric, e.g. see datetimes, where tz naive dates look like UTC 
    # upon serialization to the ISO 8601 format.
    #         diff_keys = \
    #             list( key for key in intersect_keys 
    #                     if dict1[key] != dict2[key])

    if len(diff_keys)>0: 
        log['diff_keys'] = diff_keys
        log['diffs'].update(
            dict(zip(diff_keys,([dict1[key],dict2[key]] for key in diff_keys ) )))
    
    return log


# def log_obj_update(obj_update_func):
#     
#     @transaction.atomic()
#     @wraps(obj_update_func)
#     def _inner(self, bundle, **kwargs):
#         logger.debug(str(('decorator start; log_obj_update', 
#             self,self._meta.resource_name)))
#         kwargs = kwargs or {}    
#         
#         original_bundle = Bundle(
#             data={},
#             request=bundle.request)
#         if hasattr(bundle,'obj'): 
#             original_bundle.obj = bundle.obj
#         else:
#             bundle = self._locate_obj(bundle, **kwargs)
#             original_bundle.obj = bundle.obj
#         
#         # store and compare dehydrated outputs: 
#         # the api logger is concerned with what's sent out of the system, i.e.
#         # the dehydrated output, not the internal representations.
#         
#         ## filter out the fields that aren't actually on this resource (well fields on reagent)
#         schema = self.build_schema()
#         fields = schema['fields']
#         original_bundle.data = { key: original_bundle.data[key] 
#             for key in original_bundle.data.keys() if key in fields.keys() }
#         original_bundle = self.full_dehydrate(original_bundle)
#         updated_bundle = obj_update_func(self,bundle, **kwargs)
#         updated_bundle = self.full_dehydrate(updated_bundle)
#         difflog = compare_dicts(original_bundle.data, updated_bundle.data)
# 
#         log = ApiLog()
#         log.username = bundle.request.user.username 
#         log.user_id = bundle.request.user.id 
#         log.date_time = timezone.now()
#         log.ref_resource_name = self._meta.resource_name
#         log.api_action = str((bundle.request.method)).upper()
#         log.uri = self.get_resource_uri(bundle)
#         log.key = '/'.join([str(x) for x in self.detail_uri_kwargs(bundle).values()])
#         
#         log.diff_dict_to_api_log(difflog)
# 
#         # user can specify any valid, escaped json for this field
#         if 'apilog_json_field' in bundle.data:
#             log.json_field = bundle.data['apilog_json_field']
#         
#         # TODO: abstract the form field name
#         if HEADER_APILOG_COMMENT in bundle.request.META:
#             log.comment = bundle.request.META[HEADER_APILOG_COMMENT]
#             logger.debug('log comment: %s' % log.comment)
# 
#         if 'parent_log' in kwargs:
#             log.parent_log = kwargs.get('parent_log', None)
#         
#         log.save()
#         if(logger.isEnabledFor(logging.DEBUG)):
#             logger.debug(str(('update, api log', log)) )
#         
#         return updated_bundle
#     
#     return _inner
    
# def log_obj_delete(obj_delete_func):    
#     
#     @transaction.atomic()
#     @wraps(obj_delete_func)
#     def _inner(self, bundle, **kwargs):
#         logger.debug('---log obj_delete')
#         kwargs = kwargs or {}    
#         
#         obj_delete_func(self,bundle=bundle, **kwargs)
#         
#         log = ApiLog()
#         log.username = bundle.request.user.username 
#         log.user_id = bundle.request.user.id 
#         log.date_time = timezone.now()
#         log.ref_resource_name = self._meta.resource_name
#         log.api_action = str((bundle.request.method)).upper()
#                     
#         # user can specify any valid, escaped json for this field
#         if 'apilog_json_field' in bundle.data:
#             log.json_field = json.dumps(bundle.data['apilog_json_field'])
#         log.uri = self.get_resource_uri(bundle)
#         log.key = '/'.join([str(x) for x in self.detail_uri_kwargs(bundle).values()])
# 
#         # TODO: how do we feel about passing form data in the headers?
#         # TODO: abstract the form field name
#         if HEADER_APILOG_COMMENT in bundle.request.META:
#             log.comment = bundle.request.META[HEADER_APILOG_COMMENT]
# 
#         if 'parent_log' in kwargs:
#             log.parent_log = kwargs.get('parent_log', None)
#             
#         log.save()
#         logger.debug(str(('delete, api log', log)) )
#         
#         return bundle


# def log_patch_list(patch_list_func):    
#       
#     @transaction.atomic()
#     def _inner(self, request, **kwargs):
#         logger.debug('create an apilog for the patch list: resource: %s' % self._meta.resource_name)
#         listlog = ApiLog()
#         listlog.username = request.user.username 
#         listlog.user_id = request.user.id 
#         listlog.date_time = timezone.now()
#         listlog.ref_resource_name = self._meta.resource_name
#         listlog.api_action = 'PATCH_LIST'
#         listlog.uri = self.get_resource_uri()
#         # TODO: how do we feel about passing form data in the headers?
#         # TODO: abstract the form field name
#         if HEADER_APILOG_COMMENT in request.META:
#             listlog.comment = request.META[HEADER_APILOG_COMMENT]
#          
#         listlog.save();
#         listlog.key = listlog.id
#         listlog.save()
#         
#         kwargs = kwargs or {}    
#         if not kwargs.get('parent_log', None):
#             kwargs['parent_log'] = listlog
#                   
#         response = patch_list_func(self, request, **kwargs) 
#          
#         return response        
#     return _inner


# class LoggingMixin(IccblBaseResource):
#     '''
#     Intercepts obj_create, obj_update and creates an ApiLog entry for the action
#     
#     Note: Extending classes must also define a "detail_uri_kwargs" method 
#     that returns an _ordered_dict_, since we log the kwargs as ordered args.
#     ** note: "detail_uri_kwargs" returns the set of lookup keys for the resource 
#     URI construction.
#     '''
# 
#     def make_log(self, request, **kwargs):
#         log = ApiLog()
#         log.username = request.user.username 
#         log.user_id = request.user.id 
#         log.date_time = timezone.now()
#         log.api_action = str((request.method)).upper()
# 
#         # TODO: how do we feel about passing form data in the headers?
#         # TODO: abstract the form field name
#         if HEADER_APILOG_COMMENT in request.META:
#             log.comment = request.META[HEADER_APILOG_COMMENT]
#     
#         if kwargs:
#             for key, value in kwargs.items():
#                 if hasattr(log, key):
#                     setattr(log, key, value)
#         
#         return log
#     
#     def _locate_obj(self,bundle, **kwargs):
#         # lookup the object, the same way that it would be looked up in 
#         # ModelResource.obj_update
#         if not bundle.obj or not self.get_bundle_detail_data(bundle):
#             try:
#                 lookup_kwargs = self.lookup_kwargs_with_identifiers(bundle, kwargs)
#             except:
#                 # if there is trouble hydrating the data, fall back to just
#                 # using kwargs by itself (usually it only contains a "pk" key
#                 # and this will work fine.
#                 lookup_kwargs = kwargs
# 
#             try:
#                 bundle.obj = self.obj_get(bundle=bundle, **lookup_kwargs)
#             except ObjectDoesNotExist:
#                 raise NotFound(("A model instance matching the provided "
#                                 " arguments could not be found: ", lookup_kwargs))
#         return bundle
#     
#     @log_obj_create
#     def obj_create(self, bundle, **kwargs): 
#         return super(LoggingMixin, self).obj_create(bundle, **kwargs)
#     
#     @log_obj_update
#     def obj_update(self, bundle, **kwargs):
#         return super(LoggingMixin, self).obj_update(bundle, **kwargs)
#       
#     @log_obj_delete
#     def obj_delete(self, bundle, **kwargs):
#         return super(LoggingMixin, self).obj_delete(bundle, **kwargs) 
#     
#     @log_patch_list
#     def patch_list(self, request, **kwargs):
#         return Resource.patch_list(self,request, **kwargs) 
         
        
def download_tmp_file(path, filename):
    """                                                                         
    Send a file through Django without loading the whole file into              
    memory at once. The FileWrapper will turn the file object into an           
    iterator for chunks of 8KB.                                                 
    """
    try:
        _file = file(_path)
        logger.debug(str(('download_attached_file',_path,_file)))
        wrapper = FileWrapper(_file)
        # use the same type for all files
        response = HttpResponse(wrapper, content_type='text/plain') 
        response['Content-Disposition'] = \
            'attachment; filename=%s' % unicode(filename)
        response['Content-Length'] = os.path.getsize(_path)
        return response
    except Exception,e:
        logger.error(str(('could not find attached file object for id', id, e)))
        raise e

def get_supertype_fields(resource_definition):
    supertype = resource_definition.get('supertype', None)
    if supertype:
        temp = MetaHash.objects.get(
            scope='resource', key=supertype)
        super_resource_def = temp.model_to_dict(scope='fields.resource')
        fields = get_supertype_fields(super_resource_def)
        
        fields.update(deepcopy(
            MetaHash.objects.get_and_parse(
                scope='fields.' + supertype, 
                field_definition_scope='fields.field')))
        for field in fields.values():
            if not field['table']:
                field['table'] = super_resource_def['table']
        return fields
    else:
        return {}    


# class ManagedResource(LoggingMixin):
#     '''
#     Uses the field and resource definitions in the Metahash store to determine 
#     the fields to expose for a Resource
# 
#     # NOTE if using this class, must implement the "not implemented error" methods
#     # on Resource (these are implemented with ModelResource):
#     # detail_uri_kwargs
#     # get_obj_list
#     # apply_filters
#     # obj_get_list
#     # obj_get
#     # obj_create
#     # obj_update
#     # obj_delete
#     '''
#     resource_registry = {}
#     
#     def __init__(self, field_definition_scope='fields.field', **kwargs):
#         self.resource = self._meta.resource_name
#         self.scope = 'fields.' + self.resource
#         self.field_definition_scope = field_definition_scope
#         self.meta_bootstrap_fields = ['resource_uri']
#         
#         logger.debug(str(('---init resource', 
#                           self.resource, self.scope, field_definition_scope)))
#         
#         ManagedResource.resource_registry[self.scope] = self;
# 
#         # TODO: research why calling reset_filtering_and_ordering, as below, fails        
#         metahash = MetaHash.objects.get_and_parse(
#             scope=self.scope, 
#             field_definition_scope=field_definition_scope)
#         for key,fieldhash in metahash.items():
#             self.Meta.filtering[key] = ALL_WITH_RELATIONS
#         
#         for key,fieldhash in metahash.items():
#             if 'ordering' in fieldhash and fieldhash['ordering']:
#                 self.Meta.ordering.append(key)
#         
#         super(ManagedResource,self).__init__(**kwargs)
#         self.original_fields = deepcopy(self.fields)
#         self.create_fields()
#         
#     # local method  
#     def reset_field_defs(self, scope=None):
#         if not scope:
#             scope = self.scope
#         # FIXME: somehow the registry has become keyed off "fields.[resource_name]"
#         # instead of just "resource_name"
#         if scope.find('fields') != 0: 
#             scope = 'fields.' + scope
#         if scope not in ManagedResource.resource_registry:
#             msg = str((
#                 'resource for scope not found: ', scope, self._meta.resource_name,
#                 'in resource registry',ManagedResource.resource_registry.keys(),
#                 'possible cause: resource not entered in urls.py' ))
#             logger.warn(msg)
#             raise Exception(msg)
#         resource = ManagedResource.resource_registry[scope]
# 
#         resource.clear_cache();
#                 
#         resource.create_fields();
#         resource.reset_filtering_and_ordering();
#     
#     def clear_cache(self):
#         
#         # provisional 20140825
#         logger.debug('clear cache')
#         cache.delete(self._meta.resource_name + ':schema')
#         self.field_alias_map = {}
#         
#     # local method    
#     # TODO: allow turn on/off of the reset methods for faster loading.
#     def reset_filtering_and_ordering(self):
#         self._meta.filtering = {}
#         self._meta.ordering = []
#         metahash = MetaHash.objects.get_and_parse(scope=self.scope, clear=True)
#         for key,hash in metahash.items():
#             if 'filtering' in hash and hash['filtering']:
#                 self._meta.filtering[key] = ALL
#         
#         for key,hash in metahash.items():
#             if 'ordering' in hash and hash['ordering']:
#                 self._meta.ordering.append(key)
#         logger.debug(str(('meta filtering', self._meta.filtering)))
#     
#     # local method
#     def create_fields(self):
#         
#         logger.debug(str(('--create_fields', self._meta.resource_name, 
#             self.scope, 'original fields', self.original_fields.keys() )))
#         if hasattr(self._meta, 'bootstrap_fields'):
#             logger.debug(str(('bootstrap fields', self._meta.bootstrap_fields)))
# 
#         _fields = MetaHash.objects.get_and_parse(scope=self.scope, 
#                     field_definition_scope='fields.field', clear=True)
#         logger.debug(str(('managed fields to create', _fields.keys())))
# 
#         try:
#             resource_def = MetaHash.objects.get(
#                 scope='resource', key=self._meta.resource_name)
#             resource_definition = resource_def.model_to_dict(scope='fields.resource')
#             
#             supertype_fields = get_supertype_fields(resource_definition)
#             if supertype_fields: 
#                 logger.debug('resource: %s, supertype fields: %r', 
#                     self._meta.resource_name, supertype_fields.keys())
#             
#             supertype_fields.update(_fields)
#             _fields = supertype_fields
#             for item in _fields.values():
#                 item['scope'] = 'fields.%s' % self._meta.resource_name
#             
#         except Exception, e:
#             if not getattr(self, 'suppress_errors_on_bootstrap', False):
#                 logger.exception('in create_fields: resource information not available: %r',
#                     self._meta.resource_name)
#             else:
#                 logger.info('in create_fields: resource information not available: %r',
#                     self._meta.resource_name)
#         ## build field alias table
#         self.field_alias_map = {}
#         for field_name, item in _fields.items():
#             alias = item.get('alias', None)
#             if alias:
#                 self.field_alias_map[alias] = field_name
# 
#         new_fields = {}
#         for field_name, field_obj in self.original_fields.items():
#             if field_name in _fields:
#                 new_fields[field_name] = deepcopy(field_obj)
#             elif ( hasattr(self._meta, 'bootstrap_fields') 
#                     and field_name in self._meta.bootstrap_fields ):
#                 logger.debug('====== bootstrapping field: ' + field_name)
#                 new_fields[field_name] = deepcopy(field_obj)
#             elif field_name in self.meta_bootstrap_fields:
#                 new_fields[field_name] = deepcopy(field_obj)
# 
#         unknown_keys = set(_fields.keys()) - set(new_fields.keys())
#         logger.debug(str(('managed keys not yet defined', unknown_keys)))
#         for field_name in unknown_keys:
#             field_def = _fields[field_name]
#             if 'json_field_type' in field_def and field_def['json_field_type']:
#                 # TODO: use type to create class instances
#                 # JSON fields are read only because they are hydrated in the 
#                 # hydrate_json_field method
#                 if field_def['json_field_type'] == 'fields.BooleanField':
#                     new_fields[field_name] = eval(field_def['json_field_type'])(
#                         attribute=field_name,
#                         readonly=True, blank=True, null=True, default=False ) 
#                 else:
#                     new_fields[field_name] = eval(field_def['json_field_type'])(
#                         attribute=field_name,readonly=True, blank=True, null=True) 
#             elif 'linked_field_value_field' in field_def and field_def['linked_field_value_field']:
#                 new_fields[field_name] = eval(field_def['linked_field_type'])(
#                     attribute=field_name,readonly=True, blank=True, null=True) 
#             else:
#                 logger.debug('creating unknown field as a char: ' + field_name)
#                 new_fields[field_name] = fields.CharField(
#                     attribute=field_name, readonly=True, blank=True, null=True)
# 
#         logger.debug(str((
#             'resource', self._meta.resource_name, self.scope, 
#             'create_fields done: fields created', new_fields.keys() )))
#         self.fields = new_fields
#         return self.fields
# 
#     def alias_item(self, item):
#         if self.field_alias_map:
#             new_dict =  dict(zip(
#                 (self.field_alias_map.get(key, key),val) 
#                 for (key,val) in item.items()))
#             return new_dict
#         else:
#             return item 
#         
#     def create_aliasmapping_iterator(self, data):
#         def data_generator(data):
#             for item in data:
#                 yield alias_item(data)
#             
#         if self.field_alias_map:
#             data_generator(data)
#         else:
#             return data 
# 
#     def _get_resource_def(self, resource_name=None):
#         resource_name = resource_name or self._meta.resource_name;
#         resource_def = MetaHash.objects.get(
#             scope='resource', key=resource_name)
#         _def = resource_def.model_to_dict(scope='fields.resource')
#         # content_types: all resources serve JSON and CSV
#         content_types = _def.get('content_types', None)
#         if not content_types:
#             content_types = []
#         _temp = set(content_types)
#         _temp.add('json')
#         _temp.add('csv')
#         _def['content_types'] = list(_temp)
#         return _def
# 
#  
#     def build_schema(self):
#         DEBUG_BUILD_SCHEMA = False or logger.isEnabledFor(logging.DEBUG)
# 
#         # FIXME: consider using the cache decorator or a custom memoize decorator?
#         schema = cache.get(self._meta.resource_name + ":schema")
#         if schema:
#             logger.debug(str(('====cached schema:', self._meta.resource_name)))
#             return schema
#         
#         if DEBUG_BUILD_SCHEMA:
#             logger.info('------build_schema: ' + self.scope)
#         schema = {}
#         
#         try:
#             schema['fields'] = deepcopy(
#                 MetaHash.objects.get_and_parse(
#                     scope=self.scope, field_definition_scope='fields.field'))
#             
#             if 'json_field' in schema['fields']: 
#                 # because we don't want this serialized directly (see dehydrate)
#                 schema['fields'].pop('json_field')  
#             # all TP resources have an resource_uri field
#             if not 'resource_uri' in schema['fields']:
#                 schema['fields']['resource_uri'] = { 
#                     'key': 'resource_uri',
#                     'scope': 'fields.%s' % self._meta.resource_name,
#                     'title': 'URI',
#                     'description': 'URI for the record',
#                     'data_type': 'string',
#                     'table': 'None', 
#                     'visibility':[] }
#             # all TP resources have an id field
#             if not 'id' in schema['fields']:
#                 schema['fields']['id'] = { 
#                     'key': 'id', 
#                     'scope': 'fields.%s' % self._meta.resource_name,
#                     'title': 'ID',
#                     'description': 'Internal ID for the record',
#                     'data_type': 'string',
#                     'table':'None', 
#                     'visibility':[] }
#             
#         except Exception, e:
#             if not getattr(self, 'suppress_errors_on_bootstrap', False):
#                 logger.exception('in build schema: %r',
#                     self._meta.resource_name)
#             else:
#                 logger.info('in build schema: %r',
#                     self._meta.resource_name)
#             raise e
#             
#         try:
#             ## FIXED: client can get the Resource definition from either the 
#             ## schema (here), or from the Resource endpoint; 
#             ## SO the "resource_definition" here is copied to the endpoint bundle.data
#             
#             schema['resource_definition'] = self._get_resource_def()
#             
#             if DEBUG_BUILD_SCHEMA: 
#                 logger.info(str(('content_types1', self._meta.resource_name, 
#                     schema['resource_definition']['content_types'])))
#             
#             # supertype
#             # TODO: not-recursive: json fields are not populated
#             _fields = schema['fields']
#             supertype_fields = get_supertype_fields(schema['resource_definition'])
#             logger.debug('resource: %s, supertype fields: %r', 
#                 self._meta.resource_name, supertype_fields.keys())
#             supertype_fields.update(_fields)
#             _fields = supertype_fields
#             for item in _fields.values():
#                 item['scope'] = 'fields.%s' % self._meta.resource_name
#             schema['fields'] =  _fields
#             
#             # Set:
#             # - Default field table
#             # - Field dependencies
#             logger.debug('== debugging: schema resource definition so far: %s', 
#                 schema['resource_definition'])
#             default_table = schema['resource_definition'].get('table',None)
#             if DEBUG_BUILD_SCHEMA: 
#                 logger.info(str(('default_table', default_table)))
#             for key,field in schema['fields'].items():
#                 
#                 if not field.get('table', None):
#                     field['table'] = default_table
#                 
#                 dep_fields = set()
#                 if field.get('value_template', None):
#                     dep_fields.update(
#                         re.findall(r'{([a-zA-Z0-9_-]+)}', field['value_template']))
#                 if field.get('display_options', None):
#                     dep_fields.update(
#                         re.findall(r'{([a-zA-Z0-9_-]+)}', field['display_options']))
#                 if DEBUG_BUILD_SCHEMA: 
#                     logger.info(str(('field', key, 'dependencies', dep_fields)))
#                 field['dependencies'] = dep_fields
#         except Exception, e:
#             if not getattr(self, 'suppress_errors_on_bootstrap', False):
#                 logger.exception('on build schema: %r',self._meta.resource_name)
#             else:
#                 logger.info('build_schema: resource %r, ex: %r',
#                     self._meta.resource_name, e)
#         
#         if DEBUG_BUILD_SCHEMA: 
#             logger.info('------build_schema,done: ' + self.scope ) 
#        
#         cache.set(self._meta.resource_name + ':schema', schema)
#         return schema
#     
#     def is_valid(self, bundle, request=None):
#         """
#         obj_create{ full_hydrate, save{ is_valid, save_related, save, save_m2m }}
#          
#         NOTE: not extending tastypie.Validation variations, since they don't do 
#         much, and we need access to the meta inf here anyway.
#         NOTE: since "is_valid" is called at save(), so post-hydrate, all of the 
#         defined fields have already "hydrated" the data; 
#         this is a fail-fast validation, essentially, and preempts this validation.
#         Here we will validate contextually, based on information in the metahash;
#         overridden in each resource for more specific needs.
#         
#         Performs a check on the data within the bundle (and optionally the
#         request) to ensure it is valid.
# 
#         Should return a dictionary of error messages. If the dictionary has
#         zero items, the data is considered valid. If there are errors, keys
#         in the dictionary should be field names and the values should be a list
#         of errors, even if there is only one.
#         """
#         
#         schema = self.build_schema()
#         fields = schema['fields']
#         
#         # cribbed from tastypie.validation.py - mesh data and obj values, then validate
#         data = {}
#         if bundle.obj.pk:
#             data = model_to_dict(bundle.obj)
#         if data is None:
#             data = {}
#         data.update(bundle.data)
#         
#         # do validations
#         errors = defaultdict(list)
#         
#         for name, field in fields.items():
#             keyerrors = []
#             value = data.get(name, None)
#             
#             if field.get('required', False):
#                 logger.debug(str(('check required: ', name, value)))
#                 
#                 if value is None:
#                      keyerrors.append('required')
#                 if isinstance(value, basestring):
#                     if len(value.strip()) == 0:
#                         keyerrors.append('required')
#                         
#             if not value or isinstance(value, (list, tuple)) and not value[0]:
#                 if keyerrors:
#                     errors[name] = keyerrors            
#                 continue
#             
#             ##FIXME: some vocab fields are not choices fields
#             if 'choices' in field and field['choices']:
#                 logger.debug(str(('check choices: ', name, value, field['choices'])))
#                 if field['data_type'] != 'list':
#                     if str(value) not in field['choices']: # note: comparing as string
#                         keyerrors.append(
#                             str((value, 'is not one of', field['choices'])))
#                 else:
#                     for x in value:
#                         if str(x) not in field['choices']: # note: comparing as string
#                             keyerrors.append(
#                                 str((value, 'are not members of', field['choices'])))
# 
#             if 'regex' in field and field['regex']:
#                 logger.debug(str(('check regex: ', name, value, field['regex'] )))
#                 if not re.match(field['regex'], value):
#                     msg = field.get('validation_message', None)
#                     if not msg:
#                         msg = str((value, 'failed to match the pattern', field['regex']))
#                     keyerrors.append(msg)
# 
#             if keyerrors:
#                 errors[name] = keyerrors
#                 
#         if errors:
#             bundle.errors[self._meta.resource_name] = errors
#             logger.warn(str((
#                 'bundle errors', bundle.errors, len(bundle.errors.keys()),
#                 'bundle_data', data)))
#             return False
#         return True
#         
#         
#     def dehydrate(self, bundle):
#         ''' 
#         Implementation hook method, override to augment bundle, post dehydrate
#         by the superclass used here to do the "hydrate_json_field"
#         '''
#         if len(bundle.data) == 0 : return bundle
#         
#         _fields = MetaHash.objects.get_and_parse(
#             scope=self.scope, field_definition_scope='fields.field')
#         for key in [ 
#                 x for x,y in _fields.items() if y.get('json_field_type') ]:
#             bundle.data[key] = bundle.obj.get_field(key);
#         
#         bundle.data['json_field'] = ''
#         # json_field will not be part of the public API, it is for internal use
#         bundle.data.pop('json_field') 
#         
#         return bundle
#     
#     def hydrate_json_field(self, bundle):
#         '''
#         hydrate bundle data values that will be stuffed into the json_field
#         -Note: as mentioned elsewhere, for the initial load of the 
#         Metahash:fields, fields that are JSON fields (to be stuffed into 
#         json_field) must be first defined as a record with a 
#         scope='metahash:field'; then they can be set as attribute values on 
#         other fields in the second step.
#         '''
#         
#         json_obj = {}
#         
#         # FIXME: why is clear=True here?
#         local_field_defs = MetaHash.objects.get_and_parse(
#             scope=self.scope, field_definition_scope='fields.field', clear=True)
#         
#         # Use the tastypie field type to serialize into the json_field
#         for key in [ 
#             str(x) for x,y in local_field_defs.items() 
#                 if 'json_field_type' in y and y['json_field_type'] ]:
#             if key not in self.fields:
#                 raise RuntimeError(str((
#                     'for the resource', self._meta.resource_name, 
#                     'the key to deserialize', key, 
#                     'was not defined as a resource field: fields.', 
#                     self.fields.keys() )))
#             val = bundle.data.get(key,None)
#             if val:
#                 try:
#                     if hasattr(val, "strip"): # test if it is a string
#                         val = self.fields[key].convert(
#                             smart_text(val,'utf-8', errors='ignore'))
#                     # test if it is a sequence
#                     elif hasattr(val, "__getitem__") or hasattr(val, "__iter__"): 
#                         val = [smart_text(x,'utf-8',errors='ignore') for x in val]
#                     json_obj.update({ key:val })
#                 except Exception, e:
#                     logger.exception('on hydrate json field')
#                     raise e
#         bundle.data['json_field'] = json.dumps(json_obj);
#         logger.debug(str(('--- hydrated:', bundle.data['json_field'])))
#         return bundle;
# 
#     def obj_get(self, bundle, **kwargs):
#         try:
#             bundle = super(ManagedResource, self).obj_get(bundle, **kwargs);
#             return bundle
#         except Exception, e:
#             if getattr(self, 'suppress_errors_on_bootstrap', False):
#                 logger.debug('on obj_get', exc_info=1)
#             else:
#                 logger.exception('on obj_get')
#             raise e  
# 
#     def find_key_from_resource_uri(self,resource_uri):
#         schema = self.build_schema()
#         id_attribute = resource = schema['resource_definition']['id_attribute']
#         resource_name = self._meta.resource_name + '/'
#         
#         index = resource_uri.rfind(resource_name)
#         if index > -1:
#             index = index + len(resource_name)
#             keystring = resource_uri[index:]
#         else:
#             keystring = resource_uri
#         keys = keystring.strip('/').split('/')
#         logger.info(str(('keys', keys, 'id_attribute', id_attribute)))
#         if len(keys) < len(id_attribute):
#             raise NotImplementedError(str((
#                 'resource uri does not contain all id attributes',
#                 resource_uri,'id_attributes',id_attribute)))
#         else:
#             return dict(zip(id_attribute,keys))
# 
#     def get_id(self,deserialized,**kwargs):
#         schema = self.build_schema()
#         id_attribute = schema['resource_definition']['id_attribute']
#         fields = schema['fields']
#         kwargs_for_id = {}
#         for id_field in id_attribute:
#             if deserialized and deserialized.get(id_field,None):
#                 kwargs_for_id[id_field] = parse_val(
#                     deserialized.get(id_field,None), id_field,fields[id_field]['data_type']) 
#             elif kwargs and kwargs.get(id_field,None):
#                 kwargs_for_id[id_field] = parse_val(
#                     kwargs.get(id_field,None), id_field,fields[id_field]['data_type']) 
#             elif 'resource_uri' in deserialized:
#                 return self.find_key_from_resource_uri(deserialized['resource_uri'])
#         return kwargs_for_id
# 
#     def _get_attribute(self, obj, attribute):
#         '''
#         get an attribute that is possibly defined by dot notation:
#         so reagent.substance_id will first get the reagent, then the substance_id
#         '''
#         parts = attribute.split('.')
#         
#         current_obj = obj
#         for part in parts:
#             if hasattr(current_obj,part):
#                 current_obj = getattr(current_obj, part)
#         if current_obj == obj:
#             return None
#         return current_obj 
#     
#     def _get_hashvalue(self, dictionary, attribute):
#         '''
#         get an attribute that is possibly defined by dot notation:
#         so reagent.substance_id will first get the reagent, then the substance_id
#         '''
#         parts = attribute.split('.')
#         current_val = dictionary
#         for part in parts:
#             if part in current_val:
#                 current_val = current_val[part]
#         
#         return current_val
#     
#     def _handle_500(self, request, exception):
#         logger.exception('handle_500 error: %s' % self._meta.resource_name)
#         return super(ManagedResource, self)._handle_500(request, str((type(exception), str((exception)))) )
#         
#     # override
#     def detail_uri_kwargs(self, bundle_or_obj):
#         """
#         Override resources.ModelResource
#         Given a ``Bundle`` or an object (typically a ``Model`` instance),
#         it returns the extra kwargs needed to generate a detail URI.
# 
#         By default, it uses the model's ``pk`` in order to create the URI.
#         """
#         if bundle_or_obj is None:
#             return {}
# 
#         resource_name = self._meta.resource_name
#         id_attribute = None
#         try:
#             schema = self.build_schema()
#             if 'resource_definition' not in schema:
#                 self.clear_cache()
#                 schema = self.build_schema()
#             if 'resource_definition' in schema:
#                 resource = schema['resource_definition']
#                 kwargs = OrderedDict() 
#                 if 'id_attribute' in resource:
#                     id_attribute = resource['id_attribute']
#                     for x in id_attribute:
#                         val = ''
#                         if isinstance(bundle_or_obj, Bundle):
#                             val = self._get_attribute(bundle_or_obj.obj, x)
#                         else:
#                             if hasattr(bundle_or_obj, x):
#                                 val = self._get_attribute(bundle_or_obj,x)  
#                             elif isinstance(bundle_or_obj, dict):
#                                 val = self._get_hashvalue(bundle_or_obj, x) 
#                             else:
#                                 raise Exception(
#                                     'obj %r, %r does not contain %r' 
#                                     % (type(obj), obj, x))
#                         if isinstance(val, datetime.datetime):
#                             val = val.isoformat()
#                         else:
#                             val = str(val)
#                          
#                         kwargs[x] = val
#                      
#                     return kwargs
#                 else:
#                     logger.warn('Resource: %s, "id_attribute" not yet loaded', resource_name)
#         except Exception, e:
#             logger.warn('resource: %s, id_attribute: %s, exception: %s ', resource_name,
#                 id_attribute,  e)
#             if logger.isEnabledFor(logging.INFO):
#                 try:
#                     logger.exception((
#                         'Unable to locate resource: %s has it been loaded yet?',
#                         'type: %s, id_attribute: %s'),
#                         resource_name, type(bundle_or_obj),id_attribute)
#                 except Exception, e:
#                     logger.exception('reporting exception')
#         # Fall back to base class implementation 
#         # (using the declared primary key only, for ModelResource)
#         # This is useful in order to bootstrap the ResourceResource
#         logger.debug(str((
#             'resource_definition: %s not available, use base class method' 
#             % resource_name)))
#         return super(ManagedResource,self).detail_uri_kwargs(bundle_or_obj)
# 
#     def get_via_uri(self, uri, request=None):
#         """
#         Override TP so that the resource name is optional, and is searched for 
#         from the right.
#         """
#         found_at = uri.find(self._meta.resource_name + '/')
#         if found_at == -1:
#             chomped_uri = self._meta.resource_name + '/' + uri
#         else:
#             chomped_uri = uri[found_at:]
#         try:
#             for url_resolver in getattr(self, 'urls', []):
#                 result = url_resolver.resolve(chomped_uri)
# 
#                 if result is not None:
#                     view, args, kwargs = result
#                     break
#             else:
#                 raise Resolver404("URI not found in 'self.urls'.")
#         except Resolver404:
#             raise NotFound("The URL provided '%s' was not a link to a valid resource." % uri)
#         bundle = self.build_bundle(request=request)
#         return self.obj_get(bundle=bundle, **self.remove_api_resource_names(kwargs))
#         
#     def get_local_resource_uri(
#             self, bundle_or_obj=None, url_name='api_dispatch_list'):
#         '''
#         special 'local' version of the uri - when creating the uri for 
#         containment lists (user.permissionss, for example), convert 
#         "reports/api/v1/permission/resource/read" to "permission/resource/read"
#         '''
#         uri = super(ManagedResource, self).get_resource_uri(
#             bundle_or_obj=bundle_or_obj, url_name=url_name)
#         return uri[uri.find(self._meta.resource_name):]
#     
#     def get_resource_uri(self,bundle_or_obj=None, url_name='api_dispatch_list'):
#         uri = super(ManagedResource, self).get_resource_uri(
#             bundle_or_obj=bundle_or_obj, url_name=url_name)
#         return uri
# 
#     def prepend_urls(self):
#         return [
#             url(r"^(?P<resource_name>%s)/(?P<id>[\d]+)%s$" 
#                     % (self._meta.resource_name, trailing_slash()), 
#                 self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
#             url(r"^(?P<resource_name>%s)/(?P<scope>[\w\d_.-:]+)/(?P<key>[^/]+)%s$" 
#                     % (self._meta.resource_name, trailing_slash()), 
#                 self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
#             url(r"^(?P<resource_name>%s)/(?P<key>((?=(schema))__|(?!(schema))[^/]+))%s$" 
#                     % (self._meta.resource_name, trailing_slash()), 
#                 self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
#         ]    
# 
#     def create_response(self, request, data, response_class=HttpResponse, **response_kwargs):
#         '''
#         Override to set a Content-Disposition attachment header in the response;
#         - this can be used by the browser to give the download a name.
#         - TODO: this is an expedient solution to the download button in the 
#         browser, a better solution will set the Content-Disposition from the client
#         '''
#         response = super(ManagedResource, self).create_response(request, data, response_class, **response_kwargs)
# 
#         if 'Content-Disposition' not in response:
#             format = request.GET.get('format', None)
#             if format and format.lower() != 'json':
#                 if format in self._meta.serializer.content_types:
#                     desired_format = self._meta.serializer.content_types[format]
#                     response['Content-Type'] = desired_format
#                     response['Content-Disposition'] = \
#                         'attachment; filename=%s.%s' % (self._meta.resource_name, format)
#                 
#         return response
#  
#     def _get_filename(self,schema, kwargs):
#         filekeys = []
#         if 'id_attribute' in schema:
#             filekeys.extend([ str(kwargs[key]) for 
#                 key in schema['id_attribute'] if key in kwargs ])
#         else:
#             _dict = {key:val for key,val in kwargs.items() 
#                 if key not in [
#                     'visibilities','exact_fields','api_name','resource_name',
#                     'includes','order_by']}
#             for i,(x,y) in enumerate(_dict.items()):
#                 filekeys.append(str(x))
#                 filekeys.append(str(y))
#                 if i == 10:
#                     break
#                 
#         filekeys.insert(0,self._meta.resource_name)
#         logger.info('filekeys: %r', filekeys)
#         filename = '_'.join(filekeys)
#         filename = re.sub(r'[\W]+','_',filename)
#         logger.debug('get_filename: %r, %r' % (filename, kwargs))
#         return filename

 
# class ExtensibleModelResourceMixin(ModelResource):
#     '''
#     Tastypie ModelResource mixin that passes the full request/url parsing kwargs
#     on to the underlying "get_obj_list method:    
#     Tastypie sequence:
#     Resource.get_list()-> 
#         ModelResource.obj_get_list()->  (kwargs not passed further)
#             ModelResource.build_filters()->
#             ModelResource.apply_filters()->
#                 ModelResource.get_obj_list()
#     Ordinarily, "get_obj_list" does not receive the kwargs from the base
#     Resource class - it just returns a stock query for the model.  With this 
#     modification, extra args can be passed to modfify the stock query (and 
#     return extra columns, for instance).
#     
#     Note: TP is built for extensible hooks, but this is pointing to a custom
#     Resource class implementation.
#     '''
# 
#     # Override Resoure to enable (optional) kwargs to modify the base query
#     # Provisional, move to base class
#     # get_list->obj_get_list->build_filters->apply_filters->get_obj_list
#     def obj_get_list(self, bundle, **kwargs):
#         """
#         A ORM-specific implementation of ``obj_get_list``.
# 
#         Takes an optional ``request`` object, whose ``GET`` dictionary can be
#         used to narrow the query.
#         """
#         filters = {}
# 
#         if hasattr(bundle.request, 'GET'):
#             # Grab a mutable copy.
#             filters = bundle.request.GET.copy()
# 
#         # Update with the provided kwargs.
#         filters.update(kwargs)
#         applicable_filters = self.build_filters(filters=filters)
# 
#         try:
#             # MODIFICATION: adding kwargs to the apply_filters call
#             # logger.debug(str(('kwargs', kwargs)))
#             _kwargs = kwargs
#             if 'request' in _kwargs: 
#                 _kwargs = {}
#             objects = self.apply_filters(bundle.request, applicable_filters, **_kwargs)
#             
#             if self._meta.resource_name == 'apilog':
#                 logger.debug(str(('kwargs', kwargs)))
#                 return self._meta.authorization.read_list(objects, bundle, **_kwargs)
#             else:
#                 return self.authorized_read_list(objects, bundle)
#         except ValueError, e:
#             logger.warn(str(('on obj_get_list', e)))
#             raise BadRequest(str(("Invalid resource lookup data provided (mismatched type).", e)))
#         
#     def apply_filters(self, request, applicable_filters, **kwargs): 
#         '''
#         Delegates to the parent ModelResource.apply_filters method.
#         '''       
#         query = self.get_object_list(request, **kwargs)
#         return query.apply_filters(request, applicable_filters);
# 
#     def get_object_list(self, request, **kwargs):
#         '''
#         Override this method if the kwargs will be used to modify the base query
#         returned by get_obj_list()
#         '''
#         return super(ExtensibleModelResourceMixin, self).get_object_list(request);


# class FilterModelResourceMixin(ExtensibleModelResourceMixin):
#     '''
#     Tastypie ModelResource mixin to enable "exclude" filters as well as "include"
#     filters.
#     
#     How:  Modifies the dict returned from build_filters;
#     - the new dict contains a top level: "include" and "exclude" sub dicts.
#     - "include is the same, "exlude" is for an exclude filter.
#     '''
#     
#     def build_filters(self, filters=None):
#         ''' 
#         Override of Resource - 
#         Enable excludes as well as regular filters.
#         see https://github.com/toastdriven/django-tastypie/issues/524#issuecomment-34730169
#         
#         Note: call sequence: 
#         get_list->obj_get_list->build_filters->apply_filters->get_obj_list
#         
#         '''
#         if not filters:
#             return filters
#     
#         applicable_filters = {}
#     
#         # Normal filtering
#         filter_params = dict([(x, filters[x]) 
#             for x in filter(lambda x: not x.endswith('__ne'), filters)])
#         applicable_filters['filter'] = \
#             super(FilterModelResourceMixin, self).build_filters(filter_params)
#     
#         # Exclude filtering
#         exclude_params = dict([(x[:-4], filters[x]) 
#             for x in filter(lambda x: x.endswith('__ne'), filters)])
#         applicable_filters['exclude'] = \
#             super(FilterModelResourceMixin, self).build_filters(exclude_params)
#     
#         return applicable_filters 
#        
#     def apply_filters(self, request, applicable_filters, **kwargs):
#         ''' 
#         Override of ModelResource - 
#         "applicable_filters" now contains two sub dictionaries:
#         - "filter" - normal filters
#         - "exclude" - exclude filters, to be applied serially (AND'ed)
#         '''
# 
#         query = self.get_object_list(request, **kwargs)
#         
#         f = applicable_filters.get('filter')
#         if f:
#             query = query.filter(**f)
#             
#         e = applicable_filters.get('exclude')
#         if e:
#             for exclusion_filter, value in e.items():
#                 query = query.exclude(**{exclusion_filter: value})
# 
#         return query 
    

# class ManagedModelResource(FilterModelResourceMixin, 
#                            ManagedResource, PostgresSortingResource):
#     pass


# class MetaHashResource(ManagedModelResource):
#     '''
#     This table serves as a triple/quad store;
#     Predefined fields:
#     - key field
#     - scope: secondary key, for quad store usage patterns
#     - ordinal: record ordering
#     - json_field: stores serialized json objects
#     - json_field_type: specifies a field type stored in the serialized json_field 
#     json_value storage field:
#       - these values are defined by "virtual" fields in the quad store, keyed by
#         a "field.tablename" scope.
#       - must define a json_field_type for any "virtual" field stored in the 
#         json_value field.
#     '''
#     
#     class Meta:
#         bootstrap_fields = ['scope', 'key', 'ordinal', 'json_field_type', 
#                             'json_field','linked_field_type']
#         queryset = MetaHash.objects.filter(
#             scope__startswith="fields.").order_by('scope','ordinal','key')
#         authentication = MultiAuthentication(
#             BasicAuthentication(), SessionAuthentication())
#         authorization= UserGroupAuthorization()        
#         ordering = []
#         filtering = {} #{'scope':ALL, 'key':ALL}
#         serializer = LimsSerializer()
#         excludes = [] #['json_field']
#         # this makes Backbone/JQuery happy because it likes to JSON.parse the returned data
#         always_return_data = True 
#         resource_name = 'metahash'
# 
#     def __init__(self, **kwargs):
#         super(MetaHashResource,self).__init__(**kwargs)
#     
#     def put_list(self, request, **kwargs):
#         self.suppress_errors_on_bootstrap = True
#         logger.error('supress errors on bootstrap: %r', self._meta.resource_name)
#         result = super(MetaHashResource, self).put_list(request, **kwargs)
#         self.suppress_errors_on_bootstrap = False
#         return result
#     
#     def patch_list(self, request, **kwargs):
#         self.suppress_errors_on_bootstrap = True
#         logger.error('supress errors on bootstrap: %r', self._meta.resource_name)
#         result = super(MetaHashResource, self).patch_list(request, **kwargs)
#         self.suppress_errors_on_bootstrap = False
#         return result
#     
#     def obj_create(self, bundle, **kwargs):
#         '''
#         Reset the field_defs after each create/update:
#         - find new json as they are defined, 
#         - update ordering,filtering groups
#         '''
#         bundle = super(MetaHashResource, self).obj_create(bundle, **kwargs);
#         if getattr(bundle.obj,'scope').find('fields') == 0: #'fields.field':
#             self.reset_field_defs(getattr(bundle.obj,'scope'))
#         return bundle
# 
#     def obj_update(self, bundle, **kwargs):
#         bundle = super(MetaHashResource, self).obj_update(bundle, **kwargs);
#         self.reset_field_defs(getattr(bundle.obj,'scope'))
#         return bundle
#     
#     @log_obj_delete
#     def obj_delete(self, bundle, **kwargs):
#         logger.info('delete: %s, %s', bundle, kwargs)
#         return ManagedModelResource.obj_delete(self, bundle, **kwargs)
#     
#     def obj_delete_list(self, bundle, **kwargs):
#         logger.info('obj delete list %s', kwargs)
#         ManagedModelResource.obj_delete_list(self, bundle, **kwargs)
#     
#     def is_valid(self, bundle, request=None):
#         '''
#         We need to override this to bypass when initializing
#         '''
#         result = super(MetaHashResource, self).is_valid(bundle, request=request)
#         
#         data = {}
#         if bundle.obj.pk:
#             data = model_to_dict(bundle.obj)
#         if data is None:
#             data = {}
#         data.update(bundle.data)
# 
#         name = 'regex'
#         if data.get(name, None):
#             try:
#                 re.compile(data[name])
#             except Exception, e:
#                 msg = 'invalid %r: %r, ex: %r' % (name,data[name],e)
#                 logger.warn('validation error: %s', msg)
#                 bundle.errors[self._meta.resource_name] = { name: [msg]}
#                 return False
#         
#         return result
#     
#     def hydrate(self, bundle):
#         bundle = super(MetaHashResource, self).hydrate(bundle);
#         return bundle
#     
#     def build_schema(self):
#         schema = super(MetaHashResource,self).build_schema()
#         temp = [ x.scope for x in self.Meta.queryset.distinct('scope')]
#         schema['extraSelectorOptions'] = { 
#             'label': 'Resource', 'searchColumn': 'scope', 'options': temp }
#         return schema
#         
#     def build_key(self, resource_name, data):
#         '''
#         Override, because the metahash resource is special, and will always use
#         a /scope/key/ as key
#         '''    
#         return data['scope'] + '/' + data['key']



# class ResourceResourceBak(ManagedModelResource):
#     '''
#     This resource extends the ManagedModelResource, uses the metahash table
#     internally, and has fields defined in the Metahash table.
#     '''
#     def __init__(self, **kwargs):
#         super(ResourceResourceBak,self).__init__(
#             field_definition_scope='fields.resource', **kwargs)
# 
#     class Meta:
#         '''
#         Note, does not need the 'json_field_type' since MetahashResource is 
#         managing the fields
#         '''
#         bootstrap_fields = ['scope', 'key', 'ordinal', 'json_field'] 
#         queryset = MetaHash.objects.filter(
#             scope='resource').order_by('key', 'ordinal', 'scope')
#         authentication = MultiAuthentication(
#             BasicAuthentication(), SessionAuthentication())
#         authorization= SuperUserAuthorization()        
#         ordering = []
#         filtering = {'scope':ALL, 'key': ALL, 'alias':ALL}
#         serializer = LimsSerializer()
#         excludes = [] #['json_field']
#         always_return_data = True # this makes Backbone happy
#         resource_name='resource' 
#     
#     def build_schema(self):
#         schema = super(ResourceResourceBak,self).build_schema()
#         temp = [ x.scope for x in self.Meta.queryset.distinct('key')]
#         schema['extraSelectorOptions'] = { 
#             'label': 'Resource', 'searchColumn': 'key', 'options': temp }
#         return schema
#     
#     def is_valid(self, bundle, request=None):
#         '''
#         We need to override this to bypass when initializing
#         TODO: re-examine dehydrate, same issue there.
#         '''
#         try:
#             return super(ResourceResourceBak, self).is_valid(bundle, request=request)
#         except ObjectDoesNotExist, e:
#             # notify and bypass
#             logger.warn(str(('Resources not defined', e, self._meta.resource_name)))
#             
#             return True;
# 
#     def dehydrate(self, bundle):
#         bundle = super(ResourceResourceBak,self).dehydrate(bundle)
#         # Get the fields schema for each resource
#         if ManagedResource.resource_registry.get('fields.'+bundle.obj.key, None):
#             resource = ManagedResource.resource_registry['fields.'+bundle.obj.key]
#             schema = resource.build_schema();
#             bundle.data['schema'] = schema;
#             
#             # TODO: duplicate of logic in _get_resource_def
#             _temp = set(bundle.data.get('content_types',[]))
#             _temp.add('json')
#             _temp.add('csv')
#             bundle.data['content_types'] = list(_temp)
#         else:
#             logger.error('no API resource found in the registry for ' + 
#                          'fields.'+bundle.obj.key + 
#                          '.  Cannot build the schema for this resource.' )
#         
#         # hack to make a "key" entry that will alwasy sort first, for debugging
#         bundle.data['1'] = bundle.data['key']
#         
#         return bundle
# 
#     def put_list(self, request, **kwargs):
#         self.suppress_errors_on_bootstrap = True
#         result = super(ResourceResourceBak, self).put_list(request, **kwargs)
#         self.suppress_errors_on_bootstrap = False
#         return result
#     
#     def patch_list(self, request, **kwargs):
#         self.suppress_errors_on_bootstrap = True
#         result = super(ResourceResourceBak, self).patch_list(request, **kwargs)
#         self.suppress_errors_on_bootstrap = False
#         return result
#         
#     def obj_create(self, bundle, **kwargs):
#         '''
#         Override - because the metahash resource is both a resource and the 
#         definer of json fields, reset_field_defs after each create/update, 
#         in case, new json fields are defined,or in case ordering,filtering 
#         groups are updated
#         '''
#         try:
#             bundle = super(ResourceResourceBak, self).obj_create(bundle, **kwargs);
#             if getattr(bundle.obj,'scope').find('fields') == 0: #'fields.field':
#                 self.reset_field_defs(getattr(bundle.obj,'scope'))
#             return bundle
#         except Exception, e:
#             logger.exception('on obj create')
#             raise e
#         
#     def obj_update(self, bundle, **kwargs):
#         bundle = super(ResourceResourceBak, self).obj_update(bundle, **kwargs);
#         self.reset_field_defs(getattr(bundle.obj,'scope'))
#         return bundle


class ApiLogAuthorization(UserGroupAuthorization):
    '''
    Specialized authorization, allows users to read logs for resources they are 
    authorized for.
    '''
    
    def read_list(self, object_list, bundle, ref_resource_name=None, **kwargs):
        if not ref_resource_name:
            ref_resource_name = self.resource_meta.resource_name;
        if self._is_resource_authorized(
            ref_resource_name, bundle.request.user, 'read'):
            return object_list

    def read_detail(self, object_list, bundle, ref_resource_name=None, **kwargs):
        if not ref_resource_name:
            ref_resource_name = self.resource_meta.resource_name;
        if self._is_resource_authorized(
            ref_resource_name, bundle.request.user, 'read'):
            return True



class ManagedSqlAlchemyResourceMixin(IccblBaseResource,SqlAlchemyResource):
    '''
    '''
    # FIXME: put this here temporarily until class hierarchy is refactored
    def log_patches(self,request, original_data, new_data, **kwargs):
        '''
        log differences between dicts having the same identity in the arrays:
        @param original_data - data from before the API action
        @param new_data - data from after the API action
        - dicts have the same identity if the id_attribute keys have the same
        value.
        '''
        DEBUG_PATCH_LOG = False or logger.isEnabledFor(logging.DEBUG)
        if DEBUG_PATCH_LOG:
            logger.info('log patches: %s' %kwargs)
        log_comment = None
        if HEADER_APILOG_COMMENT in request.META:
            log_comment = request.META[HEADER_APILOG_COMMENT]
            logger.debug(str(('log comment', log_comment)))
        
        if DEBUG_PATCH_LOG:
            logger.info('log patches original: %s, =====new data===== %s',
                original_data,new_data)
        schema = self.build_schema()
        id_attribute = schema['id_attribute']
        if DEBUG_PATCH_LOG:
            logger.info('===id_attribute: %s', id_attribute)
        deleted_items = list(original_data)        
        for new_dict in new_data:
            log = ApiLog()
            log.username = request.user.username 
            log.user_id = request.user.id 
            log.date_time = timezone.now()
            log.ref_resource_name = self._meta.resource_name
            log.key = '/'.join([str(new_dict[x]) for x in id_attribute])
            log.uri = '/'.join([self._meta.resource_name,log.key])
            
            # user can specify any valid, escaped json for this field
            # if 'apilog_json_field' in bundle.data:
            #     log.json_field = bundle.data['apilog_json_field']
            
            log.comment = log_comment
    
            if 'parent_log' in kwargs:
                log.parent_log = kwargs.get('parent_log', None)
            
            prev_dict = None
            for c_dict in original_data:
                if c_dict:
                    if DEBUG_PATCH_LOG:
                        logger.info('consider prev dict: %s', c_dict)
                    prev_dict = c_dict
                    for key in id_attribute:
                        if new_dict[key] != c_dict[key]:
                            prev_dict = None
                            break
                    if prev_dict:
                        break # found
            if DEBUG_PATCH_LOG:
                logger.info('prev_dict: %s, ======new_dict====: %s', prev_dict, new_dict)
            if prev_dict:
                # if found, then it is modified, not deleted
                logger.debug('remove from deleted dict %r, %r',
                    prev_dict, deleted_items)
                deleted_items.remove(prev_dict)
                
                difflog = compare_dicts(prev_dict,new_dict)
                if 'diff_keys' in difflog:
                    # log = ApiLog.objects.create()
                    log.api_action = str((request.method)).upper()
                    log.diff_dict_to_api_log(difflog)
                    log.save()
                    if DEBUG_PATCH_LOG:
                        logger.info('update, api log: %r' % log)
                else:
                    # don't save the log
                    if DEBUG_PATCH_LOG:
                        logger.info('no diffs found: %r, %r, %r' 
                            % (prev_dict,new_dict,difflog))
            else: # creating
                log.api_action = API_ACTION_CREATE
                log.added_keys = json.dumps(new_dict.keys())
                log.diffs = json.dumps(new_dict)
                log.save()
                if DEBUG_PATCH_LOG:
                    logger.info('create, api log: %s', log)
                
        for deleted_dict in deleted_items:
            log = ApiLog()
            log.comment = log_comment
            log.username = request.user.username 
            log.user_id = request.user.id 
            log.date_time = timezone.now()
            log.ref_resource_name = self._meta.resource_name
            log.key = '/'.join([str(deleted_dict[x]) for x in id_attribute])
            log.uri = '/'.join([self._meta.resource_name,log.key])
        
            # user can specify any valid, escaped json for this field
            # if 'apilog_json_field' in bundle.data:
            #     log.json_field = bundle.data['apilog_json_field']
            
            log.comment = log_comment
    
            if 'parent_log' in kwargs:
                log.parent_log = kwargs.get('parent_log', None)

            log.api_action = API_ACTION_DELETE
            log.diff_keys = json.dumps(deleted_dict.keys())
            log.diffs = json.dumps(deleted_dict)
            log.save()
            if DEBUG_PATCH_LOG:
                logger.info('delete, api log: %r',log)
            

class ApiResource(ManagedSqlAlchemyResourceMixin,SqlAlchemyResource):
    '''
    Provides framework for "Patch" and "Put" methods
    - patch_list, put_list, put_detail; with logging
    - patch_detail must be implemented
    - patch/put methods call "patch_obj"
    - "patch_obj" must be implemented
    - "put" methods call "delete_obj" 
    - "delete_obj" must be implemented
    - wrap mutating methods in "un_cache"
    - prepend_urls must direct to the detail/list methods
    '''
    
    def __init__(self, **kwargs):
        super(ApiResource,self).__init__(**kwargs)
        self.resource_resource = None

    def get_resource_resource(self):
        if not self.resource_resource:
            self.resource_resource = ResourceResource()
        return self.resource_resource

    
    def get_schema(self, request, **kwargs):
    
        desired_format = self.determine_format(request)
        serialized = self.serialize(request, self.build_schema(), desired_format)
        return HttpResponse(
            content=serialized, 
            content_type=build_content_type(desired_format))
    
    def build_schema(self):
        logger.debug('build schema for: %r', self._meta.resource_name)
        return self.get_resource_resource().get_resource_schema(self._meta.resource_name)

    def deserialize(self, request, data, format='application/json'):
        logger.info('apiResource deserialize...')
        return self._meta.serializer.deserialize(
            data, 
            format=request.META.get('CONTENT_TYPE', 'application/json'))

    def get_resource_uri(self, deserialized, **kwargs):
        ids = [self._meta.resource_name]
        ids.extend(self.get_id(deserialized,**kwargs).values())
        return '/'.join(ids)
        
    def get_id(self,deserialized,**kwargs):
        schema = self.build_schema()
        id_attribute = schema['id_attribute']
        fields = schema['fields']
        kwargs_for_id = {}
        for id_field in id_attribute:
            if deserialized and deserialized.get(id_field,None):
                kwargs_for_id[id_field] = parse_val(
                    deserialized.get(id_field,None), id_field,fields[id_field]['data_type']) 
            elif kwargs and kwargs.get(id_field,None):
                kwargs_for_id[id_field] = parse_val(
                    kwargs.get(id_field,None), id_field,fields[id_field]['data_type']) 
            elif 'resource_uri' in deserialized:
                return self.find_key_from_resource_uri(deserialized['resource_uri'])
        return kwargs_for_id

    def find_key_from_resource_uri(self,resource_uri):
        schema = self.build_schema()
        id_attribute = schema['id_attribute']
        resource_name = self._meta.resource_name + '/'
         
        index = resource_uri.rfind(resource_name)
        if index > -1:
            index = index + len(resource_name)
            keystring = resource_uri[index:]
        else:
            keystring = resource_uri
        keys = keystring.strip('/').split('/')
        logger.info(str(('keys', keys, 'id_attribute', id_attribute)))
        if len(keys) < len(id_attribute):
            raise NotImplementedError(str((
                'resource uri does not contain all id attributes',
                resource_uri,'id_attributes',id_attribute)))
        else:
            return dict(zip(id_attribute,keys))

    def parse(self,deserialized):
        schema = self.build_schema()
        fields = schema['fields']
        mutable_fields = [ field for field in fields.values() 
            if field.get('editability', None) and (
                'u' in field['editability'] or 'c' in field['editability'])]
        logger.debug('r: %r, mutable fields: %r', self._meta.resource_name, 
            [field['key'] for field in mutable_fields])
        initializer_dict = {}
        for field in mutable_fields:
            key = field['key']
            if key in deserialized:
                initializer_dict[key] = parse_val(
                    deserialized.get(key,None), key,field['data_type']) 
        return initializer_dict
    
    @write_authorization
    @un_cache        
    def patch_list(self, request, **kwargs):

        logger.info('patch list, user: %r, resource: %r' 
            % ( request.user.username, self._meta.resource_name))
        logger.debug('patch list: %r' % kwargs)

        deserialized = self.deserialize(request,request.body)
        
        if not self._meta.collection_name in deserialized:
            raise BadRequest("Invalid data sent, must be nested in '%s'" 
                % self._meta.collection_name)
        deserialized = deserialized[self._meta.collection_name]
        logger.debug('-----deserialized: %r', deserialized)
        # Look for id's kwargs, to limit the potential candidates for logging
        schema = self.build_schema()
        id_attribute = schema['id_attribute']
        kwargs_for_log = kwargs.copy()
        logger.debug('id_attribute: %r', id_attribute)
        for id_field in id_attribute:
            ids = set()
            # Test for each id key; it's ok on create for ids to be None
            for _dict in [x for x in deserialized if x.get(id_field, None)]:
                ids.add(_dict.get(id_field))
            if ids:
                kwargs_for_log['%s__in'%id_field] = LIST_DELIMITER_URL_PARAM.join(ids)
        # get original state, for logging
        logger.debug('kwargs_for_log: %r', kwargs_for_log)
        original_data = self._get_list_response(request,**kwargs_for_log)
        try:
            with transaction.atomic():
                
                for _dict in deserialized:
                    self.patch_obj(_dict)
        except ValidationError as e:
            logger.exception('Validation error: %r', e)
            raise ImmediateHttpResponse(response=self.error_response(request, e.errors))
            
        # get new state, for logging
        new_data = self._get_list_response(request,**kwargs_for_log)
        logger.debug('new data: %s'% new_data)
        logger.debug('patch list done, new data: %d' 
            % (len(new_data)))
        self.log_patches(request, original_data,new_data,**kwargs)
        
        if not self._meta.always_return_data:
            return http.HttpAccepted()
        else:
            response = self.get_list(request, **kwargs)             
            response.status_code = 201
            return response
 
    @write_authorization
    @un_cache        
    def put_list(self,request, **kwargs):
        # TODO: enforce a policy that either objects are patched or deleted
        #         raise NotImplementedError('put_list must be implemented')
            
        deserialized = self.deserialize(request,request.body)
        if not self._meta.collection_name in deserialized:
            raise BadRequest("Invalid data sent, must be nested in '%s'" 
                % self._meta.collection_name)
        deserialized = deserialized[self._meta.collection_name]
        
        # Look for id's kwargs, to limit the potential candidates for logging
        schema = self.build_schema()
        id_attribute = resource = schema['id_attribute']
        kwargs_for_log = kwargs.copy()
        for id_field in id_attribute:
            ids = set()
            # Test for each id key; it's ok on create for ids to be None
            for _dict in [x for x in deserialized if x.get(id_field, None)]:
                ids.add(_dict.get(id_field))
            if ids:
                kwargs_for_log['%s__in'%id_field] = LIST_DELIMITER_URL_PARAM.join(ids)
        # get original state, for logging
        original_data = self._get_list_response(request,**kwargs_for_log)

        logger.debug('put list %s, %s',deserialized,kwargs)
        try:
            with transaction.atomic():
                
                # TODO: review REST actions:
                # PUT deletes the endpoint
                
                self._meta.queryset.delete()
                
                for _dict in deserialized:
                    self.put_obj(_dict)
        except ValidationError as e:
            logger.exception('Validation error: %r', e)
            raise ImmediateHttpResponse(response=self.error_response(request, e.errors))

        # get new state, for logging
        kwargs_for_log = kwargs.copy()
        for id_field in id_attribute:
            ids = set()
            # After patch, the id keys must be present
            for _dict in [x for x in deserialized]:
                ids.add(_dict.get(id_field))
            if ids:
                kwargs_for_log['%s__in'%id_field] = LIST_DELIMITER_URL_PARAM.join(ids)
        new_data = self._get_list_response(request,**kwargs_for_log)
        
        logger.debug('new data: %s'% new_data)
        logger.debug('patch list done, new data: %d' 
            % (len(new_data)))
        self.log_patches(request, original_data,new_data,**kwargs)

        if not self._meta.always_return_data:
            return http.HttpAccepted()
        else:
            response = self.get_list(request, **kwargs)             
            response.status_code = 200
            return response 

    @write_authorization
    @un_cache        
    def post_list(self, request, **kwargs):
        # NOTE: POST-ing will always be for single items
        # - this is because tastypie interprets url's with no pk as "list" urls
        return self.post_detail(request,**kwargs)
        
    @write_authorization
    @un_cache        
    def post_detail(self, request, **kwargs):
        return self.patch_detail(request,**kwargs)
        
    @write_authorization
    @un_cache        
    def put_detail(self, request, **kwargs):
                
        # TODO: enforce a policy that either objects are patched or deleted
        raise NotImplementedError('put_detail must be implemented')

        deserialized = self.deserialize(request,request.body)

        logger.debug('put detail: %r, %r' % (deserialized,kwargs))
        
        # cache state, for logging
        # Look for id's kwargs, to limit the potential candidates for logging
        schema = self.build_schema()
        id_attribute = schema['id_attribute']
        kwargs_for_log = {}
        for id_field in id_attribute:
            if deserialized.get(id_field,None):
                kwargs_for_log[id_field] = deserialized[id_field]
            elif kwargs.get(id_field,None):
                kwargs_for_log[id_field] = kwargs[id_field]
        logger.debug('put detail: %s, %s' %(deserialized,kwargs_for_log))
        if not kwargs_for_log:
            # then this is a create
            original_data = []
        else:
            original_data = self._get_list_response(request,**kwargs_for_log)
        
        try:
            with transaction.atomic():
                logger.debug('call put_obj')
                obj = self.put_obj(deserialized, **kwargs)
        except ValidationError as e:
            logger.exception('Validation error: %r', e)
            raise ImmediateHttpResponse(response=self.error_response(request, e.errors))
                
        if not kwargs_for_log:
            for id_field in id_attribute:
                val = getattr(obj, id_field,None)
                kwargs_for_log['%s' % id_field] = val
        # get new state, for logging
        new_data = self._get_list_response(request,**kwargs_for_log)
        self.log_patches(request, original_data,new_data,**kwargs)
        
        if not self._meta.always_return_data:
            return http.HttpAccepted()
        else:
            response.status_code = 200
            return response

    @write_authorization
    @un_cache        
    def patch_detail(self, request, **kwargs):

        deserialized = self.deserialize(request,request.body)

        logger.debug('patch detail %s, %s', deserialized,kwargs)

        # cache state, for logging
        # Look for id's kwargs, to limit the potential candidates for logging
        schema = self.build_schema()
        id_attribute = schema['id_attribute']
        kwargs_for_log = {}
        try:
            kwargs_for_log = self.get_id(deserialized,**kwargs)
            logger.debug('patch detail: %s, %s' %(deserialized,kwargs_for_log))
        except Exception:
            # this can be ok, if the ID is generated
            logger.info('object id not posted')
        if not kwargs_for_log:
            # then this is a create
            original_data = []
        else:
            original_data = []
            try:
                item = self._get_detail_response(request,**kwargs_for_log)
                if item:
                    original_data = [item]
            except Exception, e: 
                logger.exception('exception when querying for existing obj: %s', kwargs_for_log)
                original_data = []
        try:
            with transaction.atomic():
                obj = self.patch_obj(deserialized, **kwargs)
                for id_field in id_attribute:
                    val = getattr(obj, id_field,None)
                    if val:
                        kwargs_for_log['%s' % id_field] = val
        except ValidationError as e:
            logger.exception('Validation error: %r', e)
            raise ImmediateHttpResponse(response=self.error_response(request, e.errors))

        # get new state, for logging
        new_data = [self._get_detail_response(request,**kwargs_for_log)]
        self.log_patches(request, original_data,new_data,**kwargs)

        if not self._meta.always_return_data:
            return http.HttpAccepted()
        else:
            response = self.get_detail(request,**kwargs_for_log)
            response.status_code = 201
            return response

    @write_authorization
    @un_cache        
    def delete_list(self, request, **kwargs):
        raise NotImplementedError('delete_list is not implemented for %s'
            % self._meta.resource_name )

    @write_authorization
    @un_cache        
    def delete_detail(self, request, **kwargs):

        logger.debug('delete_detail: %s,  %s' 
            % (self._meta.resource_name, kwargs))

        # cache state, for logging
        # Look for id's kwargs, to limit the potential candidates for logging
        schema = self.build_schema()
        id_attribute = schema['id_attribute']
        kwargs_for_log = {}
        for id_field in id_attribute:
            if kwargs.get(id_field,None):
                kwargs_for_log[id_field] = kwargs[id_field]
        logger.debug('delete detail: %s' %(kwargs_for_log))
        if not kwargs_for_log:
            raise Exception('required id keys %s' % id_attribute)
        else:
            original_data = self._get_detail_response(request,**kwargs_for_log)

        with transaction.atomic():
            
            self.delete_obj(**kwargs_for_log)

        # Log
        # TODO: consider log_patches
        
        logger.info('deleted: %s' %kwargs_for_log)
        log_comment = None
        if HEADER_APILOG_COMMENT in request.META:
            log_comment = request.META[HEADER_APILOG_COMMENT]
            logger.debug(str(('log comment', log_comment)))
        
        schema = self.build_schema()
        id_attribute = schema['id_attribute']

        log = ApiLog()
        log.username = request.user.username 
        log.user_id = request.user.id 
        log.date_time = timezone.now()
        log.ref_resource_name = self._meta.resource_name
        log.key = '/'.join([str(original_data[x]) for x in id_attribute])
        log.uri = '/'.join([self._meta.resource_name,log.key])
    
        # user can specify any valid, escaped json for this field
        # if 'apilog_json_field' in bundle.data:
        #     log.json_field = bundle.data['apilog_json_field']
        
        log.comment = log_comment

        if 'parent_log' in kwargs:
            log.parent_log = kwargs.get('parent_log', None)
    
        log.api_action = API_ACTION_DELETE
        log.added_keys = json.dumps(original_data.keys(),cls=DjangoJSONEncoder)
        log.diffs = json.dumps(original_data,cls=DjangoJSONEncoder)
        log.save()
        logger.info(str(('delete, api log', log)) )

        return HttpNoContent()

    @un_cache        
    @transaction.atomic()    
    def put_obj(self,deserialized, **kwargs):
        try:
            self.delete_obj(deserialized, **kwargs)
        except ObjectDoesNotExist,e:
            pass 
        
        return self.patch_obj(deserialized, **kwargs)            

    def delete_obj(self, deserialized, **kwargs):
        raise NotImplementedError('delete obj must be implemented')
    
    def patch_obj(self,deserialized, **kwargs):
        raise NotImplementedError('patch obj must be implemented')

    def validate(self, _dict, patch=False):
        '''
        Perform validation according the the field schema:
        @param patch if False then check all fields (for required); not just the 
        patched fields (use if object is being created). When patching, only 
        need to check the fields that are present in the _dict
        
        @return a dict of field_key->[erors] where errors are string messages
        
        #TODO: create vs update validations: validate that create-only
        fields are not updated
        '''
        DEBUG_VALIDATION = False or logger.isEnabledFor(logging.DEBUG)
        schema = self.build_schema()
        fields = schema['fields']
        id_attribute = schema['id_attribute']
        
        # do validations
        errors = {}
        
        for name, field in fields.items():
            if name == 'resource_uri':
                continue
            
            keyerrors = []
            if patch:
                if name not in _dict:
                    continue
                else: 
                    if name in id_attribute:
                        continue
                    editability = field.get('editability',None)
                    if not editability or 'u' not in editability:
                        errors[name] = 'cannot be changed'
                        continue
                
            value = _dict.get(name,None)
            
            
            if DEBUG_VALIDATION:
                logger.info('validate: %r:%r',name,value)
                
            if field.get('required', False):
                if value is None:
                     keyerrors.append('required')
                if isinstance(value, basestring):
                    if len(value.strip()) == 0:
                        keyerrors.append('required')
                        
            if not value or isinstance(value, (list, tuple)) and not value[0]:
                if keyerrors:
                    errors[name] = keyerrors            
                continue
            
            ##FIXME: some vocab fields are not choices fields
            if 'choices' in field and field['choices']:
                if field['data_type'] != 'list':
                    if str(value) not in field['choices']: # note: comparing as string
                        keyerrors.append(
                            "'%s' is not one of %r" % (value, field['choices']))
                else:
                    for x in value:
                        if str(x) not in field['choices']: # note: comparing as string
                            keyerrors.append(
                                '%r are not members of %r' % (value, field['choices']))

            if 'regex' in field and field['regex']:
                logger.debug('name: %s, value: %s check regex: %s', name, value, field['regex'] )
                # FIXME validate regex on input
                matcher = re.compile(field['regex'])
                if field['data_type'] != 'list':
                    if not matcher.match(value):
                        msg = field.get('validation_message', None)
                        if not msg:
                            msg = "'%s' does not match pattern: '%s'" % (value, field['regex'])
                        keyerrors.append(msg)
                else:
                    for x in value:
                        if not matcher.match(x):
                            msg = field.get('validation_message', None)
                            if not msg:
                                msg = "'%s' does not match pattern: '%s'" % (x, field['regex'])
                            keyerrors.append(msg)

            if keyerrors:
                errors[name] = keyerrors

            if DEBUG_VALIDATION:
                logger.info('validate: %r:%r - %r',name,value,keyerrors)
                
        if errors:
            logger.warn('errors in submitted data: %r, errs: %s', _dict, errors)
        return errors


class ApiLogResource(ApiResource):
    
    class Meta:
        queryset = ApiLog.objects.all().order_by(
            'ref_resource_name', 'username','date_time')
        authentication = MultiAuthentication(
            BasicAuthentication(), SessionAuthentication())
        authorization= ApiLogAuthorization() #Authorization()        
        ordering = []
        filtering = {'username':ALL, 'uri': ALL, 'ref_resource_name':ALL}
        serializer = LimsSerializer()
        excludes = [] #['json_field']
        always_return_data = True # this makes Backbone happy
        resource_name='apilog' 
        max_limit = 100000
    
    def __init__(self, **kwargs):
        self.scope = 'fields.apilog'
        super(ApiLogResource,self).__init__(**kwargs)

    def prepend_urls(self):
        return [
            # override the parent "base_urls" so that we don't need to worry 
            # about schema again
            url(r"^(?P<resource_name>%s)/schema%s$" 
                % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('get_schema'), name="api_get_schema"),
            url(r"^(?P<resource_name>%s)/clear_cache%s$" 
                % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_clear_cache'), name="api_clear_cache"),
            url(r"^(?P<resource_name>%s)/(?P<id>[\d]+)%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
            url(r"^(?P<resource_name>%s)/(?P<id>[\d]+)/children%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_apilog_childview'), name="api_dispatch_apilog_childview"),
            url((r"^(?P<resource_name>%s)/children/(?P<ref_resource_name>[\w\d_.\-:]+)"
                 r"/(?P<key>[\w\d_.\-\+: \/]+)"
                 r"/(?P<date_time>[\w\d_.\-\+:]+)%s$")
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_apilog_childview2'), name="api_dispatch_apilog_childview2"),
            url((r"^(?P<resource_name>%s)/(?P<ref_resource_name>[\w\d_.\-:]+)"
                 r"/(?P<key>[\w\d_.\-\+: \/]+)"
                 r"/(?P<date_time>[\w\d_.\-\+:]+)%s$")
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
        ]    

    def dispatch_clear_cache(self, request, **kwargs):
        self.clear_cache()
        desired_format = self.determine_format(request)
        content_type=build_content_type(desired_format)
        return HttpResponse(content='ok',content_type=content_type)

    def get_detail(self, request, **kwargs):
        logger.info(str(('get_detail')))

        id = kwargs.get('id', None)
        if id:
            return self.get_list(request, **kwargs)
            
        ref_resource_name = kwargs.get('ref_resource_name', None)
        if not ref_resource_name:
            logger.info(str(('no ref_resource_name provided')))
            raise NotImplementedError('must provide a ref_resource_name parameter')
        
        key = kwargs.get('key', None)
        if not key:
            logger.info(str(('no key provided')))
            raise NotImplementedError('must provide a key parameter')
        
        date_time = kwargs.get('date_time', None)
        if not date_time:
            logger.info(str(('no date_time provided')))
            raise NotImplementedError('must provide a date_time parameter')

        kwargs['visibilities'] = kwargs.get('visibilities', ['d'])
        kwargs['is_for_detail']=True
        return self.build_list_response(request, **kwargs)
        
    def get_list(self,request,**kwargs):

        kwargs['visibilities'] = kwargs.get('visibilities', ['l'])

        return self.build_list_response(request, **kwargs)

        
    def build_list_response(self,request, **kwargs):
        DEBUG_GET_LIST = False or logger.isEnabledFor(logging.DEBUG)

        parent_log_id = None
        if 'parent_log_id' in kwargs:
            parent_log_id = kwargs.pop('parent_log_id')
            
        param_hash = {}
        param_hash.update(kwargs)
        param_hash.update(self._convert_request_to_dict(request))

        if parent_log_id:
            kwargs['parent_log_id'] = parent_log_id

        is_for_detail = kwargs.pop('is_for_detail', False)
             
        schema = super(ApiLogResource,self).build_schema()
        
        filename = self._get_filename(schema, kwargs)

        id = param_hash.pop('id', None)
        if id:
            param_hash['id__eq'] = id
        
        ref_resource_name = param_hash.pop('ref_resource_name', None)
        if ref_resource_name:
            param_hash['ref_resource_name__eq'] = ref_resource_name

        key = param_hash.pop('key', None)
        if key:
            param_hash['key__eq'] = key

        date_time = param_hash.pop('date_time', None)
        if date_time:
            param_hash['date_time__eq'] = date_time

        try:
            
            # general setup
          
            manual_field_includes = set(param_hash.get('includes', []))
            if DEBUG_GET_LIST: 
                logger.info(str(('manual_field_includes', manual_field_includes)))
  
            (filter_expression, filter_fields) = SqlAlchemyResource.\
                build_sqlalchemy_filters(schema, param_hash=param_hash)

            if filter_expression is None and 'parent_log_id' not in kwargs:
                msgs = { 'ApiLogResource': 
                    'can only service requests with filter expressions' }
                logger.info(str((msgs)))
                raise ImmediateHttpResponse(response=self.error_response(request,msgs))
                                  
            field_hash = self.get_visible_fields(
                schema['fields'], filter_fields, manual_field_includes, 
                param_hash.get('visibilities'), 
                exact_fields=set(param_hash.get('exact_fields',[])))
              
            order_params = param_hash.get('order_by',[])
            order_clauses = SqlAlchemyResource.\
                build_sqlalchemy_ordering(order_params, field_hash)
             
            rowproxy_generator = None
            if param_hash.get(HTTP_PARAM_USE_VOCAB,False):
                rowproxy_generator = IccblBaseResource.\
                    create_vocabulary_rowproxy_generator(field_hash)
 
            # specific setup 
            base_query_tables = ['reports_apilog']
            
            custom_columns = {
                #  create a full ISO-8601 date format
                'parent_log_uri': literal_column(
                    "parent_log.ref_resource_name "
                    "|| '/' || parent_log.key || '/' "
                    "|| to_char(parent_log.date_time, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS') "
                    "|| to_char(extract('timezone_hour' from parent_log.date_time),'S00')" 
                    "||':'" 
                    "|| to_char(extract('timezone_minute' from parent_log.date_time),'FM00')" 
                    ).label('parent_log_uri'),
                'child_logs': literal_column(
                    "(select count(*) from reports_apilog ra where ra.parent_log_id=reports_apilog.id)"
                    ).label('child_logs')
            }
            
            if 'date_time' in filter_fields:
                # ISO 8601 only supports millisecond precision, 
                # but postgres supports microsecond
                custom_columns['date_time'] = \
                    literal_column("date_trunc('millisecond',reports_apilog.date_time)")
            
            columns = self.build_sqlalchemy_columns(
                field_hash.values(), base_query_tables=base_query_tables,
                custom_columns=custom_columns )

            # build the query statement

            _log = self.bridge['reports_apilog']
            _log2 = self.bridge['reports_apilog']
            _log2 = _log2.alias('parent_log')
            
            j = join(_log, _log2, _log.c.parent_log_id == _log2.c.id, isouter=True )
            
            stmt = select(columns.values()).select_from(j)
            
            if 'parent_log_id' in kwargs:
                stmt = stmt.where(_log2.c.id == kwargs.pop('parent_log_id'))

            # general setup
             
            (stmt,count_stmt) = self.wrap_statement(
                stmt,order_clauses,filter_expression )
            
            # authorization filter
            if not request.user.is_superuser:
                # FIXME: "read" is too open
                # - grant read level access on a case-by-case basis
                # i.e. for screen.status updates
                resources = UserGroupAuthorization.get_authorized_resources(
                    request.user, 'read')
                stmt = stmt.where(column('ref_resource_name').in_(resources))
            
            if not order_clauses:
                stmt = stmt.order_by('ref_resource_name','key', 'date_time')
            
            title_function = None
            if param_hash.get(HTTP_PARAM_USE_TITLES, False):
                title_function = lambda key: field_hash[key]['title']
            
            return self.stream_response_from_statement(
                request, stmt, count_stmt, filename, 
                field_hash=field_hash, 
                param_hash=param_hash,
                is_for_detail=is_for_detail,
                rowproxy_generator=rowproxy_generator,
                title_function=title_function  )
             
        except Exception, e:
            logger.exception('on get_list')
            raise e  
    
    def build_schema(self):
        schema = super(ApiLogResource,self).build_schema()
        temp = [ x.key for x in 
            MetaHash.objects.all().filter(scope='resource').distinct('key')]
        schema['extraSelectorOptions'] = { 
            'label': 'Resource', 
            'searchColumn': 'ref_resource_name', 'options': temp }
        return schema        
    
    # Legacy TP methods
#     def get_resource_uri(self,bundle_or_obj=None):
#         '''
#         for efficiency, return a localized URI:
#         /apilog/k1/k2/.../kn
#         '''
#         parts = [self._meta.resource_name]
#         if bundle_or_obj is not None:
#             id_kwarg_ordered = self.detail_uri_kwargs(bundle_or_obj)
#             parts.extend(id_kwarg_ordered.values())
#         return '/'.join(parts)
        
    def dispatch_apilog_childview(self, request, **kwargs):
        kwargs['parent_log_id'] = kwargs.pop('id')
        return ApiLogResource().dispatch('list', request, **kwargs)    

    def dispatch_apilog_childview2(self, request, **kwargs):
        logger.info(str(('kwargs', kwargs)))
#         ref_resource_name = kwargs.pop('ref_resource_name')
#         key = kwargs.pop('key')
#         date_time = kwargs.pop('date_time')
        
#         date_time = dateutil.parser.parse(date_time)
#         logger.info(str(('childview2', ref_resource_name, key, date_time)))
#  
#         from django.db.models import Lookup
#          
#         class IsoDateEqual(Lookup):
#             lookup_name = 'iso_date_equal'
#          
#             def as_sql(self, compiler, connection):
#                 lhs, lhs_params = self.process_lhs(compiler, connection)
#                 rhs, rhs_params = self.process_rhs(compiler, connection)
#                 params = lhs_params + rhs_params
#                 return "date_trunc('millisecond', %s') = %s" % (lhs, rhs), params        
#         from django.db.models.fields import Field
#         Field.register_lookup(IsoDateEqual)
#          
#         parent_log = ApiLog.objects.get(ref_resource_name=ref_resource_name, 
#             key=key, date_time__iso_date_equal=date_time)
        
        
        parent_log = self._get_detail_response(request,**kwargs)
        logger.info(str(('parent_log', parent_log)))

        ref_resource_name = kwargs.pop('ref_resource_name')
        key = kwargs.pop('key')
        date_time = kwargs.pop('date_time')

        kwargs['parent_log_id'] = parent_log['id']
        return ApiLogResource().dispatch('list', request, **kwargs)    


class FieldResource(ApiResource):
    
    class Meta:
        
        queryset = MetaHash.objects.filter(
            scope__startswith="fields.").order_by('scope','ordinal','key')
        authentication = MultiAuthentication(
            BasicAuthentication(), SessionAuthentication())
        authorization= UserGroupAuthorization()        
        ordering = []
        filtering = {} 
        serializer = LimsSerializer()
        excludes = [] 
        always_return_data = True 
        resource_name = 'field'

    def __init__(self, **kwargs):
        super(FieldResource,self).__init__(**kwargs)

    def prepend_urls(self):

        return [
            url(r"^(?P<resource_name>%s)/schema%s$" 
                % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('get_schema'), name="api_get_schema"),
            url(r"^(?P<resource_name>%s)/(?P<scope>[\w\d_]+)/(?P<key>[\w\d_]+)%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
        ]
    
    def create_fields(self):
        pass
#     
#     def get_schema(self, request, **kwargs):
#     
#         desired_format = self.determine_format(request)
#         serialized = self.serialize(request, self.build_schema(), desired_format)
#         return HttpResponse(
#             content=serialized, 
#             content_type=build_content_type(desired_format))

    def build_schema(self):
        # start with the default schema for bootstrapping
        default_field = {
            'data_type': 'string',
            'editability': ['c','u'],
            'table': 'reports_metahash',
        }
        
        default_schema = {
            'key':  {
                'key': 'key',
                'scope': 'fields.field',
                'ordinal': 1,
                'json_field_type': '',
                'data_type': 'string',
            },
            'scope':  {
                'key': 'scope',
                'scope': 'fields.field',
                'ordinal': 2,
                'json_field_type': '',
                'data_type': 'string',
                
            },
            'ordinal':  {
                'key': 'ordinal',
                'scope': 'fields.field',
                'ordinal': 3,
                'json_field_type': '',
                'data_type': 'integer',
                
            },
            'json_field_type':  {
                'key': 'json_field_type',
                'scope': 'fields.field',
                'ordinal': 4,
                'json_field_type': '',
                'data_type': 'string',
                
            },
        }
        
        field_schema = deepcopy(
            MetaHash.objects.get_and_parse(
                scope='fields.field', field_definition_scope='fields.field',clear=True))
        for key,val in field_schema.items():
            for k,v in default_field.items():
                if k not in val or val.get(k)==None:
                    val[k] = v
        # do not allow the default values to be changed
        for key, val in default_schema.items():
            if key in field_schema:
                field_schema[key].update(default_schema[key])
            else:
                field_schema[key] = default_schema[key]
        
        field_schema['resource_uri'] = { 
                'key': 'resource_uri',
                'scope': 'fields.%s' % self._meta.resource_name,
                'title': 'URI',
                'description': 'URI for the record',
                'data_type': 'string',
                'table': 'None', 
                'visibility':[] 
        }
        
        # TODO: the ResourceResource should create the schema; 
        # provide one here for the bootstrap case
        schema = {
            'content_types': ['json'],
            'description': 'The fields resource',
            'id_attribute': ['scope','key'],
            'key': 'field',
            'scope': 'resource',
            'table': 'metahash',
            'title_attribute': ['scope','key'],
            'ordinal': 0,
            'resource_uri': BASE_URI +'/resource/field',
            'api_name': 'reports',
            'supertype': '',
            'fields': field_schema,
        }
        temp = [ x.scope for x in self.Meta.queryset.distinct('scope')]
        schema['extraSelectorOptions'] = { 
            'label': 'Resource', 'searchColumn': 'scope', 'options': temp }

        return schema
    
    def get_detail(self, request, **kwargs):

        kwargs['visibilities'] = kwargs.get('visibilities', ['d'])
        kwargs['is_for_detail']=True
        return self.build_list_response(request, **kwargs)
        
    @read_authorization
    def get_list(self,request,**kwargs):

        kwargs['visibilities'] = kwargs.get('visibilities', ['l'])
        return self.build_list_response(request, **kwargs)

    @read_authorization
    def build_list_response(self,request, **kwargs):

        param_hash = {}
        param_hash.update(kwargs)
        param_hash.update(self._convert_request_to_dict(request))
        
        logger.debug('param_hash: %r', param_hash)
        
        # Do not have real filtering, but support the scope filters, manually
        scope = param_hash.get('scope', None)
        if not scope:
            scope = param_hash.get('scope__exact', None)
        
        key = param_hash.get('key', None)
        if not scope:
            scopes = MetaHash.objects.all().filter(
                scope__icontains='fields.').values_list('scope').distinct()
            if not scopes.exists():
                # bootstrap case
                scopes = [('fields.field',)]
        else:
            scopes = [(scope,)]
        fields = []
        for (scope,) in scopes:
            field_hash = deepcopy(
                MetaHash.objects.get_and_parse(
                    scope=scope, field_definition_scope='fields.field', clear=True))
            fields.extend(field_hash.values())
        for field in fields:
            field['1'] = field['scope']
            field['2'] = field['key']
        
        response_hash = None
        if scope and key:
            for field in fields:
                if field['key'] == key:
                    response_hash = field
                    break
            if not response_hash:
                logger.info('Field %s/%s not found' % (scope,key))
                raise Http404('Field %s/%s not found' % (scope,key))
        else:    
            decorated = [(x['scope'],x['ordinal'],x['key'], x) for x in fields]
            decorated.sort(key=itemgetter(0,1,2))
            fields = [field for scope,ordinal,key,field in decorated]
            # TODO: generalized pagination, sort, filter
            response_hash = { 
                'meta': { 'limit': 0, 'offset': 0, 'total_count': len(fields) }, 
                self._meta.collection_name: fields 
            }
        desired_format = self.determine_format(request)
        serialized = self.serialize(request, response_hash, desired_format)
        return HttpResponse(
            content=serialized, 
            content_type=build_content_type(desired_format))

    @write_authorization
    @un_cache        
    def delete_list(self, request, **kwargs):
        MetaHash.objects.all().filter(scope__icontains='fields.').delete()

    @transaction.atomic()    
    @un_cache        
    def delete_obj(self, deserialized, **kwargs):
        
        id_kwargs = self.get_id(deserialized,**kwargs)
        logger.info('delete: %r', id_kwargs)
        MetaHash.objects.get(**id_kwargs).delete()
    
    @transaction.atomic()    
    def patch_obj(self,deserialized, **kwargs):
        
        logger.debug('deserialized: %r', deserialized)
        schema = self.build_schema()
        fields = schema['fields']
        initializer_dict = {}
        for key in fields.keys():
            if key in deserialized:
                initializer_dict[key] = parse_val(
                    deserialized.get(key,None), key,fields[key].get('data_type','string')) 
        
        id_kwargs = self.get_id(deserialized,**kwargs)
        
        try:
            field = None
            try:
                field = MetaHash.objects.get(**id_kwargs)
                errors = self.validate(deserialized, patch=True)
                if errors:
                    raise ValidationError(errors)
            except ObjectDoesNotExist, e:
                logger.debug('Metahash field %s does not exist, creating', id_kwargs)
                field = MetaHash(**id_kwargs)
                errors = self.validate(deserialized, patch=False)
                if errors:
                    raise ValidationError(errors)

            for key,val in initializer_dict.items():
                if hasattr(field,key):
                    setattr(field,key,val)
            
            if field.json_field:
                json_obj = json.loads(field.json_field)
            else:
                json_obj = {}
            
            for key,val in initializer_dict.items():
                fieldinformation = fields[key]
                # FIXME: now that tastypie is removed, json_field_type should equal data_type
                if fieldinformation.get('json_field_type', None):
                    json_field_type = fieldinformation.get('json_field_type', None)
                    if json_field_type == 'fields.CharField':
                        json_obj[key] = parse_val(val, key, 'string')
                    elif json_field_type == 'fields.ListField':
                        json_obj[key] = parse_val(val, key, 'list')
                    elif json_field_type == 'CsvBooleanField':                    
                        json_obj[key] = parse_val(val, key, 'boolean')
                    elif json_field_type == 'fields.BooleanField':
                        json_obj[key] = parse_val(val, key, 'boolean')
                    elif json_field_type == 'fields.IntegerField':
                        json_obj[key] = parse_val(val, key, 'integer')
                    elif json_field_type == 'fields.DateField':
                        raise NotImplementedError
                    elif json_field_type == 'fields.DecimalField':
                        raise NotImplementedError
                    elif json_field_type == 'fields.FloatField':
                        raise NotImplementedError
                    else:
                        raise NotImplementedError('unknown json_field_type: %s' % json_field_type)
                    
            field.json_field = json.dumps(json_obj)
            
            logger.debug('save: %r, as %r', deserialized, field)
            field.save()
                    
            logger.debug('patch_obj done')
            return field
            
        except Exception, e:
            logger.exception('on patch detail')
            raise e  


class ResourceResource(ApiResource):
    
    class Meta:
        queryset = MetaHash.objects.filter(
            scope="resource").order_by('scope','ordinal','key')
        authentication = MultiAuthentication(
            BasicAuthentication(), SessionAuthentication())
        authorization= UserGroupAuthorization()        
        ordering = []
        filtering = {} 
        serializer = LimsSerializer()
        excludes = [] 
        always_return_data = True 
        resource_name = 'resource'

    def __init__(self, **kwargs):
        super(ResourceResource,self).__init__(**kwargs)
        self.field_resource = None
        
    def get_field_resource(self):
        if not self.field_resource:
            self.field_resource = FieldResource()
        return self.field_resource
    
    def prepend_urls(self):

        return [
            url(r"^(?P<resource_name>%s)/schema%s$" 
                % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('get_schema'), name="api_get_schema"),
            url(r"^(?P<resource_name>%s)/(?P<key>[\w\d_.\-\+: ]+)%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
        ]
        
    def create_fields(self):
        pass
    
    def get_schema(self, request, **kwargs):
    
        desired_format = self.determine_format(request)
        serialized = self.serialize(request, self.build_schema(), desired_format)
        return HttpResponse(
            content=serialized, 
            content_type=build_content_type(desired_format))

    def clear_cache(self):
        ApiResource.clear_cache(self)
        cache.delete('resources');
        
    def _get_list_response(self, request, key=None, **kwargs):
        resources = cache.get('resources')
        if not resources:
            resources =  ApiResource._get_list_response(self, request)
            cache.set('resources', resources)
        
        if key:
            return [resource for resource in resources if resource['key']==key]
        else:
            return resources
        
    def get_resource_schema(self,key):
        # get the resource fields
        request = HttpRequest()
        class User:
            @staticmethod
            def is_superuser():
                return true
        request.user = User
        
        temp = self._get_list_response(request=request, key=key)
        assert len(temp) < 2, 'ResourceResource returns multiple objects for key: %r, %r' %(key,temp)
        assert len(temp)==1, 'ResourceResource returns no objects for key: %r' %key
        return temp[0]
    
    def build_schema(self):

        # get the resource fields
        request = HttpRequest()
        class User:
            @staticmethod
            def is_superuser():
                return true
        request.user = User
        resource_fields = self.get_field_resource()._get_list_response(
            request=request, scope='fields.resource')
        # build a hash out of the fields
        field_hash = {}
        for field in resource_fields:
            field_hash[field['key']]=field
        # default schema for bootstrap
        resource_schema = {
            'content_types': ['json'],
            'description': 'The fields resource',
            'id_attribute': ['scope','key'],
            'key': 'resource',
            'scope': 'resource',
            'table': 'metahash',
            'title_attribute': ['key'],
            'ordinal': 0,
            'resource_uri': BASE_URI + '/resource/field',
            'api_name': 'reports',
            'supertype': '',
            'fields': field_hash
        }
        
        return resource_schema
    
    def get_detail(self, request, **kwargs):

        kwargs['visibilities'] = kwargs.get('visibilities', ['d'])
        kwargs['is_for_detail']=True
        return self.build_list_response(request, **kwargs)
        
    @read_authorization
    def get_list(self,request,**kwargs):

        kwargs['visibilities'] = kwargs.get('visibilities', ['l'])
        return self.build_list_response(request, **kwargs)

    @read_authorization
    def build_list_response(self,request, **kwargs):

        param_hash = {}
        param_hash.update(kwargs)
        param_hash.update(self._convert_request_to_dict(request))
        
        # TODO: CACHE
        
        resources = deepcopy(
            MetaHash.objects.get_and_parse(
                scope='resource', field_definition_scope='fields.resource', clear=True))
        # if there are no resources, use self to bootstrap
        if not resources:
            resource = self.build_schema()
            resources = { resource['key']: resource }
            
        # get all of the fields
        all_fields = self.get_field_resource()._get_list_response(request=request)
        field_hash = {}
        # build a hash out of the fields
        for field in all_fields:
            _fields = field_hash.get(field['scope'],{})
            _fields[field['key']]=field
            field_hash[field['scope']] = _fields
        
        # for each resource, pull in the fields of the supertype resource
        # todo recursion
        
        for key,resource in resources.items():
            resource['1'] = resource['key']
            resource['fields'] = field_hash.get('fields.%s'%key, {})
            resource['resource_uri'] = '/'.join([
                self._meta.resource_name,resource['key']
            ])
            
            # set the field['table'] 
            for field in resource['fields'].values():
                if not field.get('table',None):
                    field['table'] = resource.get('table', None)
            
            supertype = resource.get('supertype', None)
            if supertype:
                if supertype in resources:
                    logger.debug('find the supertype fields: %r for %r', supertype, key)
                    supertype_fields = field_hash.get('fields.%s'%supertype,None)
                    if not supertype_fields:
                        # ok, if the supertype is not built yet
                        logger.warning('no fields for supertype: %r, %r', supertype, field_hash.keys())
                    else:
                        # explicitly copy out all supertype fields, then update 
                        # with fields from the current resource
                        inherited_fields = {}
                        for field in supertype_fields.values():
                            inherited_field = deepcopy(field)
                            inherited_field['scope'] = 'fields.%s' % resource['key']
                            if not inherited_field.get('table',None):
                                inherited_field['table'] = resources[supertype].get('table', None)
                            
                            inherited_fields[inherited_field['key']] = inherited_field
                        inherited_fields.update(resource['fields'])
                        resource['fields'] = inherited_fields
                else:
                    logger.error('supertype: %r, not found in resources: %r', supertype, resources.keys())
            
            resource['content_types'].append('csv')
        # TODO: extend with class specific implementations
            
        # TODO: pagination, sort, filter
       
        # only filter by key and scope at this point
        key = param_hash.get('key', None)
        if key:
            if key not in resources:
                raise Http404('Resource not found: %r' % key)
            response_hash = resources[key]
        else:
            values = resources.values()
            values.sort(key=lambda resource: resource['key'])
            response_hash = { 
                'meta': { 'limit': 0, 'offset': 0, 'total_count': len(values) }, 
                self._meta.collection_name: values
            }
        
        desired_format = self.determine_format(request)
        serialized = self.serialize(request, response_hash, desired_format)
        return HttpResponse(
            content=serialized, 
            content_type=build_content_type(desired_format))

    @write_authorization
    @un_cache        
    def delete_list(self, request, **kwargs):
        MetaHash.objects.all().filter(scope='resource').delete()

    @transaction.atomic()    
    @un_cache        
    def delete_obj(self, deserialized, **kwargs):
        
        id_kwargs = self.get_id(deserialized,**kwargs)
        logger.info('delete: %r', id_kwargs)
        MetaHash.objects.get(**id_kwargs).delete()
    
    @transaction.atomic()    
    @un_cache        
    def patch_obj(self,deserialized, **kwargs):
        
        logger.info('patch_obj: %r', deserialized)
        schema = self.build_schema()
        fields = schema['fields']
        initializer_dict = {}
        for key in fields.keys():
            if key in deserialized:
                initializer_dict[key] = parse_val(
                    deserialized.get(key,None), key,fields[key].get('data_type','string')) 
        
        id_kwargs = self.get_id(deserialized,**kwargs)
        logger.info('id_kwargs: %r', id_kwargs)
        try:
            field = None
            try:
                field = MetaHash.objects.get(**id_kwargs)
                errors = self.validate(deserialized, patch=True)
                if errors:
                    raise ValidationError(errors)
            except ObjectDoesNotExist, e:
                logger.info('Metahash resource %s does not exist, creating', id_kwargs)
                field = MetaHash(**id_kwargs)
                errors = self.validate(deserialized, patch=False)
                if errors:
                    raise ValidationError(errors)

            for key,val in initializer_dict.items():
                if hasattr(field,key):
                    setattr(field,key,val)
            
            if field.json_field:
                json_obj = json.loads(field.json_field)
            else:
                json_obj = {}
            
            for key,val in initializer_dict.items():
                fieldinformation = fields[key]
                if fieldinformation.get('json_field_type', None):
                    json_field_type = fieldinformation.get('json_field_type', None)
                    if json_field_type == 'fields.CharField':
                        json_obj[key] = parse_val(val, key, 'string')
                    elif json_field_type == 'fields.ListField':
                        json_obj[key] = parse_val(val, key, 'list')
                    elif json_field_type == 'CsvBooleanField':                    
                        json_obj[key] = parse_val(val, key, 'boolean')
                    elif json_field_type == 'fields.BooleanField':
                        json_obj[key] = parse_val(val, key, 'boolean')
                    elif json_field_type == 'fields.IntegerField':
                        json_obj[key] = parse_val(val, key, 'integer')
                    elif json_field_type == 'fields.DateField':
                        raise NotImplementedError
                    elif json_field_type == 'fields.DecimalField':
                        raise NotImplementedError
                    elif json_field_type == 'fields.FloatField':
                        raise NotImplementedError
                    else:
                        raise NotImplementedError('unknown json_field_type: %s' % json_field_type)
                    
            field.json_field = json.dumps(json_obj)
            
            field.save()
                    
            logger.info('patch_obj done')
            return field
            
        except Exception, e:
            logger.exception('on patch detail')
            raise e  


class VocabulariesResource(ApiResource):
    '''
    '''
    def __init__(self, **kwargs):
        super(VocabulariesResource,self).__init__(**kwargs)

    class Meta:
        bootstrap_fields = ['scope', 'key', 'ordinal', 'json_field']
        queryset = Vocabularies.objects.all().order_by('scope', 'ordinal', 'key')
        authentication = MultiAuthentication(
            BasicAuthentication(), SessionAuthentication())
        authorization= UserGroupAuthorization() #SuperUserAuthorization()        
        ordering = []
        filtering = {'scope':ALL, 'key': ALL, 'alias':ALL}
        serializer = LimsSerializer()
        excludes = [] #['json_field']
        always_return_data = True 
        resource_name = 'vocabularies'
        max_limit = 10000
    
    def build_schema(self):
        schema = super(VocabulariesResource,self).build_schema()
        temp = [ x.scope for x in self.Meta.queryset.distinct('scope')]
        schema['extraSelectorOptions'] = { 
            'label': 'Vocabulary', 'searchColumn': 'scope', 'options': temp }
        return schema
    
    def get_detail(self, request, **kwargs):
        key = kwargs.get('key', None)
        if not key:
            logger.info(str(('no key provided')))
            raise NotImplementedError('must provide a key parameter')
        
        scope = kwargs.get('scope', None)
        if not item_group:
            logger.info(str(('no scope provided')))
            raise NotImplementedError('must provide a scope parameter')
        
        kwargs['visibilities'] = kwargs.get('visibilities', ['d'])
        kwargs['is_for_detail']=True
        return self.build_list_response(request, **kwargs)
        
    @read_authorization
    def get_list(self,request,**kwargs):

        kwargs['visibilities'] = kwargs.get('visibilities', ['l'])

        return self.build_list_response(request, **kwargs)

        
    def build_list_response(self,request, **kwargs):
        ''' 
        Overrides tastypie.resource.Resource.get_list for an SqlAlchemy implementation
        @returns django.http.response.StreamingHttpResponse 
        '''
        DEBUG_GET_LIST = False or logger.isEnabledFor(logging.DEBUG)

        param_hash = {}
        param_hash.update(kwargs)
        param_hash.update(self._convert_request_to_dict(request))
        
        is_for_detail = kwargs.pop('is_for_detail', False)

        schema = self.build_schema()

        filename = self._get_filename(schema, kwargs)

        key = param_hash.pop('key', None)
        if key:
            param_hash['key__eq'] = key

        scope = param_hash.pop('scope', None)
        if scope:
            param_hash['scope__eq'] = scope
        
        try:
            
            # general setup
          
            manual_field_includes = set(param_hash.get('includes', []))
            
            if DEBUG_GET_LIST: 
                logger.info(str(('manual_field_includes', manual_field_includes)))
  
            (filter_expression, filter_fields) = \
                SqlAlchemyResource.build_sqlalchemy_filters(schema, param_hash=param_hash)
            
            if DEBUG_GET_LIST: 
                logger.info('filter_fields: %r, kwargs: %r', filter_fields,kwargs)
            
            
            original_field_hash = schema['fields']
            # Add convenience fields "1" and "2", which aid in viewing with json viewers
            original_field_hash['1'] = {
                'key': '1',
                'scope': 'fields.vocabularies',
                'data_type': 'string',
                'json_field_type': 'convenience_field',
                'ordering': 'false',
                'visibilities': []
                }
            original_field_hash['2'] = {
                'key': '2',
                'scope': 'fields.vocabularies',
                'data_type': 'string',
                'json_field_type': 'convenience_field',
                'ordering': 'false',
                'visibilities': []
                }
            original_field_hash['resource_uri'] = {
                'key': 'resource_uri',
                'scope': 'fields.vocabularies',
                'data_type': 'string',
                'json_field_type': 'convenience_field',
                'ordering': 'false',
                'visibilities': []
                }
            fields_for_sql = { key:field for key, field in original_field_hash.items() 
                if not field.get('json_field_type',None) }
            fields_for_json = { key:field for key, field in original_field_hash.items() 
                if field.get('json_field_type',None) }
            
            field_hash = self.get_visible_fields(
                fields_for_sql, filter_fields, manual_field_includes, 
                param_hash.get('visibilities',[]), 
                exact_fields=set(param_hash.get('exact_fields',[])))
            field_hash['json_field'] = {
                'key': 'json_field',
                'scope': 'fields.vocabularies',
                'data_type': 'string',
                'table': 'reports_vocabularies',
                'field': 'json_field',
                'ordering': 'false',
                'visibilities': ['l','d']
                }
              
            order_params = param_hash.get('order_by',[])
            order_clauses = SqlAlchemyResource.build_sqlalchemy_ordering(
                order_params, field_hash)
            
            # PROTOTYPE: to be refactored for other json_field resources
            def json_field_rowproxy_generator(cursor):
                '''
                Wrap connection cursor to fetch fields embedded in the 'json_field'
                '''
                class Row:
                    def __init__(self, row):
                        self.row = row
                        self.json_content = json.loads(row['json_field'])
                    def has_key(self, key):
                        return (key in fields_for_json or self.row.has_key(key))
                    def keys(self):
                        return self.row.keys() + fields_for_json.keys();
                    def __getitem__(self, key):
                        if key == '1':
                            return row['scope']
                        elif key == '2':
                            return row['key']
                        elif key == 'resource_uri':
                            return '/'.join(['vocabularies', row['scope'], row['key']])
                        elif key not in row:
                            if key in fields_for_json:
                                if key not in self.json_content:
                                    logger.debug(
                                        'key %r not found in json content %r', 
                                        key, self.json_content)
                                    return None
                                else:
                                    return self.json_content[key]
                            else:
                                return None
                        else:
                            return self.row[key]
                for row in cursor:
                    yield Row(row)
                    
            # specific setup
            _vocab = self.bridge['reports_vocabularies']
            custom_columns = {
                'json_field' : literal_column('json_field')
                }
            base_query_tables = ['reports_vocabularies'] 
            columns = self.build_sqlalchemy_columns(
                field_hash.values(), base_query_tables=base_query_tables,
                custom_columns=custom_columns )
            j = _vocab
            stmt = select(columns.values()).select_from(j)

            # general setup
            (stmt,count_stmt) = self.wrap_statement(stmt,order_clauses,filter_expression )
            
            title_function = None
            if param_hash.get(HTTP_PARAM_USE_TITLES, False):
                title_function = lambda key: field_hash[key]['title']
            
            return self.stream_response_from_statement(
                request, stmt, count_stmt, filename, 
                field_hash=original_field_hash, 
                param_hash=param_hash,
                is_for_detail=is_for_detail,
                rowproxy_generator=json_field_rowproxy_generator,
                title_function=title_function  )
             
        except Exception, e:
            logger.exception('on get list')
            raise e  
    
    def _get_vocabularies_by_scope(self, scope):
        ''' Utility method
        Retrieve and cache all of the vocabularies in a two level dict
        - keyed by [scope][key]
        '''
        vocabularies = cache.get('vocabularies');
        if not vocabularies:
            vocabularies = {}
            kwargs = {
                'limit': '0'
            }
            request=HttpRequest()
            request.session = Session()
            class User:
                @staticmethod
                def is_superuser():
                    return true
            request.user = User
            _data = self._get_list_response(request=request,**kwargs)
            for v in _data:
                _scope = v['scope']
                if _scope not in vocabularies:
                     vocabularies[_scope] = {}
                     logger.info('created vocab scope: %r', _scope)
                vocabularies[_scope][v['key']] = v
                
            # Hack: activity.type is serviceactivity.type + activity.class
            vocabularies['activity.type'] = deepcopy(vocabularies['serviceactivity.type'])
            vocabularies['activity.type'].update(deepcopy(vocabularies['activity.class']))
            
            cache.set('vocabularies', vocabularies);
        if scope in vocabularies:
            return deepcopy(vocabularies[scope])
        else:
            logger.warn(str(('---unknown vocabulary scope:', scope)))
            return {}
    
    def clear_cache(self):
        super(VocabulariesResource,self).clear_cache()
        cache.delete('vocabularies');

    @write_authorization
    @un_cache        
    def delete_list(self, request, **kwargs):
        Vocabularies.objects.all().delete()

    @un_cache
    def put_list(self, request, **kwargs):
        self.suppress_errors_on_bootstrap = True
        result = super(VocabulariesResource, self).put_list(request, **kwargs)
        self.suppress_errors_on_bootstrap = False
        return result
    
#     @un_cache
#     def patch_list(self, request, **kwargs):
#         self.suppress_errors_on_bootstrap = True
#         result = super(VocabulariesResource, self).patch_list(request, **kwargs)
#         self.suppress_errors_on_bootstrap = False
#         return result

    @transaction.atomic()    
    def delete_obj(self, deserialized, **kwargs):
        
        id_kwargs = self.get_id(deserialized,**kwargs)
        logger.info('delete: %r', id_kwargs)
        MetaHash.objects.get(**id_kwargs).delete()
    
    @transaction.atomic()    
    def patch_obj(self,deserialized, **kwargs):
        
        schema = self.build_schema()
        fields = schema['fields']
        initializer_dict = {}
        for key in fields.keys():
            if key in deserialized:
                initializer_dict[key] = parse_val(
                    deserialized.get(key,None), key,fields[key].get('data_type','string')) 
        
        id_kwargs = self.get_id(deserialized,**kwargs)
        
        try:
            vocab = None
            try:
                vocab = Vocabularies.objects.get(**id_kwargs)
                errors = self.validate(deserialized, patch=True)
                if errors:
                    raise ValidationError(errors)
            except ObjectDoesNotExist, e:
                logger.debug('Vocab %s does not exist, creating', id_kwargs)
                vocab = Vocabularies(**id_kwargs)
                errors = self.validate(deserialized, patch=False)
                if errors:
                    raise ValidationError(errors)

            for key,val in initializer_dict.items():
                if hasattr(vocab,key):
                    setattr(vocab,key,val)
            
            if vocab.json_field:
                json_obj = json.loads(vocab.json_field)
            else:
                json_obj = {}
            
            for key,val in initializer_dict.items():
                fieldinformation = fields[key]
                if fieldinformation.get('json_field_type', None):
                    json_field_type = fieldinformation.get('json_field_type', None)
                    if json_field_type == 'fields.CharField':
                        json_obj[key] = parse_val(val, key, 'string')
                    elif json_field_type == 'fields.ListField':
                        json_obj[key] = parse_val(val, key, 'list')
                    elif json_field_type == 'CsvBooleanField':                    
                        json_obj[key] = parse_val(val, key, 'boolean')
                    elif json_field_type == 'fields.BooleanField':
                        json_obj[key] = parse_val(val, key, 'boolean')
                    elif json_field_type == 'fields.IntegerField':
                        json_obj[key] = parse_val(val, key, 'integer')
                    elif json_field_type == 'fields.DateField':
                        raise NotImplementedError
                    elif json_field_type == 'fields.DecimalField':
                        raise NotImplementedError
                    elif json_field_type == 'fields.FloatField':
                        raise NotImplementedError
                    else:
                        raise NotImplementedError('unknown json_field_type: %s' % json_field_type)
                    
            vocab.json_field = json.dumps(json_obj)
            
            logger.debug('save: %r, as %r', deserialized, vocab)
            vocab.save()
                    
            return vocab
            
        except Exception, e:
            logger.exception('on patch detail')
            raise e  

# class VocabulariesResourceBak(ApiResource):
#     '''
#     This resource extends the ManagedModelResource using a new table 
#     (vocabularies) but has fields defined in the Metahash table.
#     '''
#     def __init__(self, **kwargs):
#         super(VocabulariesResourceBak,self).__init__(**kwargs)
# 
#     class Meta:
#         bootstrap_fields = ['scope', 'key', 'ordinal', 'json_field']
#         queryset = Vocabularies.objects.all().order_by('scope', 'ordinal', 'key')
#         authentication = MultiAuthentication(
#             BasicAuthentication(), SessionAuthentication())
#         authorization= UserGroupAuthorization() #SuperUserAuthorization()        
#         ordering = []
#         filtering = {'scope':ALL, 'key': ALL, 'alias':ALL}
#         serializer = LimsSerializer()
#         excludes = [] #['json_field']
#         always_return_data = True # this makes Backbone happy
#         resource_name = 'vocabularies'
#         max_limit = 10000
#     
#     def build_schema(self):
#         schema = super(VocabulariesResourceBak,self).build_schema()
#         temp = [ x.scope for x in self.Meta.queryset.distinct('scope')]
#         schema['extraSelectorOptions'] = { 
#             'label': 'Vocabulary', 'searchColumn': 'scope', 'options': temp }
#         return schema
#     
#     def get_detail(self, request, **kwargs):
#         key = kwargs.get('key', None)
#         if not key:
#             logger.info(str(('no key provided')))
#             raise NotImplementedError('must provide a key parameter')
#         
#         scope = kwargs.get('scope', None)
#         if not item_group:
#             logger.info(str(('no scope provided')))
#             raise NotImplementedError('must provide a scope parameter')
#         
#         kwargs['visibilities'] = kwargs.get('visibilities', ['d'])
#         kwargs['is_for_detail']=True
#         return self.build_list_response(request, **kwargs)
#         
#     @read_authorization
#     def get_list(self,request,**kwargs):
# 
#         kwargs['visibilities'] = kwargs.get('visibilities', ['l'])
# 
#         return self.build_list_response(request, **kwargs)
# 
#         
#     def build_list_response(self,request, **kwargs):
#         ''' 
#         Overrides tastypie.resource.Resource.get_list for an SqlAlchemy implementation
#         @returns django.http.response.StreamingHttpResponse 
#         '''
#         DEBUG_GET_LIST = False or logger.isEnabledFor(logging.DEBUG)
# 
#         param_hash = {}
#         param_hash.update(kwargs)
#         param_hash.update(self._convert_request_to_dict(request))
#         
#         is_for_detail = kwargs.pop('is_for_detail', False)
# 
#         schema = self.build_schema()
# 
#         filename = self._get_filename(schema, kwargs)
# 
#         key = param_hash.pop('key', None)
#         if key:
#             param_hash['key__eq'] = key
# 
#         scope = param_hash.pop('scope', None)
#         if scope:
#             param_hash['scope__eq'] = scope
#         
#         try:
#             
#             # general setup
#           
#             manual_field_includes = set(param_hash.get('includes', []))
#             
#             if DEBUG_GET_LIST: 
#                 logger.info(str(('manual_field_includes', manual_field_includes)))
#   
#             (filter_expression, filter_fields) = \
#                 SqlAlchemyResource.build_sqlalchemy_filters(schema, param_hash=param_hash)
#             
#             original_field_hash = schema['fields']
#             # Add convenience fields "1" and "2", which aid in viewing with json viewers
#             original_field_hash['1'] = {
#                 'key': '1',
#                 'scope': 'fields.vocabularies',
#                 'data_type': 'string',
#                 'json_field_type': 'convenience_field',
#                 'ordering': 'false',
#                 'visibilities': []
#                 }
#             original_field_hash['2'] = {
#                 'key': '2',
#                 'scope': 'fields.vocabularies',
#                 'data_type': 'string',
#                 'json_field_type': 'convenience_field',
#                 'ordering': 'false',
#                 'visibilities': []
#                 }
#             original_field_hash['resource_uri'] = {
#                 'key': 'resource_uri',
#                 'scope': 'fields.vocabularies',
#                 'data_type': 'string',
#                 'json_field_type': 'convenience_field',
#                 'ordering': 'false',
#                 'visibilities': []
#                 }
#             fields_for_sql = { key:field for key, field in original_field_hash.items() 
#                 if not field.get('json_field_type',None) }
#             fields_for_json = { key:field for key, field in original_field_hash.items() 
#                 if field.get('json_field_type',None) }
#             
#             field_hash = self.get_visible_fields(
#                 fields_for_sql, filter_fields, manual_field_includes, 
#                 param_hash.get('visibilities',[]), 
#                 exact_fields=set(param_hash.get('exact_fields',[])))
#             field_hash['json_field'] = {
#                 'key': 'json_field',
#                 'scope': 'fields.vocabularies',
#                 'data_type': 'string',
#                 'table': 'reports_vocabularies',
#                 'field': 'json_field',
#                 'ordering': 'false',
#                 'visibilities': ['l','d']
#                 }
#               
#             order_params = param_hash.get('order_by',[])
#             order_clauses = SqlAlchemyResource.build_sqlalchemy_ordering(
#                 order_params, field_hash)
#             
#             # PROTOTYPE: to be refactored for other json_field resources
#             def json_field_rowproxy_generator(cursor):
#                 '''
#                 Wrap connection cursor to fetch fields embedded in the 'json_field'
#                 '''
#                 class Row:
#                     def __init__(self, row):
#                         self.row = row
#                         self.json_content = json.loads(row['json_field'])
#                     def has_key(self, key):
#                         return (key in fields_for_json or self.row.has_key(key))
#                     def keys(self):
#                         return self.row.keys() + fields_for_json.keys();
#                     def __getitem__(self, key):
#                         if key == '1':
#                             return row['scope']
#                         elif key == '2':
#                             return row['key']
#                         elif key == 'resource_uri':
#                             return '/'.join(['vocabularies', row['scope'], row['key']])
#                         elif key not in row:
#                             if key in fields_for_json:
#                                 if key not in self.json_content:
#                                     logger.debug(
#                                         'key %r not found in json content %r', 
#                                         key, self.json_content)
#                                     return None
#                                 else:
#                                     return self.json_content[key]
#                             else:
#                                 return None
#                         else:
#                             return self.row[key]
#                 for row in cursor:
#                     yield Row(row)
#                     
#             # specific setup
#             _vocab = self.bridge['reports_vocabularies']
#             custom_columns = {
#                 'json_field' : literal_column('json_field')
#                 }
#             base_query_tables = ['reports_vocabularies'] 
#             columns = self.build_sqlalchemy_columns(
#                 field_hash.values(), base_query_tables=base_query_tables,
#                 custom_columns=custom_columns )
#             j = _vocab
#             stmt = select(columns.values()).select_from(j)
# 
#             # general setup
#             (stmt,count_stmt) = self.wrap_statement(stmt,order_clauses,filter_expression )
#             
#             title_function = None
#             if param_hash.get(HTTP_PARAM_USE_TITLES, False):
#                 title_function = lambda key: field_hash[key]['title']
#             
#             return self.stream_response_from_statement(
#                 request, stmt, count_stmt, filename, 
#                 field_hash=original_field_hash, 
#                 param_hash=param_hash,
#                 is_for_detail=is_for_detail,
#                 rowproxy_generator=json_field_rowproxy_generator,
#                 title_function=title_function  )
#              
#         except Exception, e:
#             logger.exception('on get list')
#             raise e  
#     
#     def _get_vocabularies_by_scope(self, scope):
#         ''' Utility method
#         Retrieve and cache all of the vocabularies in a two level dict
#         - keyed by [scope][key]
#         '''
#         vocabularies = cache.get('vocabularies');
#         if not vocabularies:
#             vocabularies = {}
#             kwargs = {
#                 'limit': '0'
#             }
#             request=HttpRequest()
#             request.session = Session()
#             class User:
#                 @staticmethod
#                 def is_superuser():
#                     return true
#             request.user = User
#             _data = self._get_list_response(request=request,**kwargs)
#             for v in _data:
#                 _scope = v['scope']
#                 if _scope not in vocabularies:
#                      vocabularies[_scope] = {}
#                      logger.info('created vocab scope: %r', _scope)
#                 vocabularies[_scope][v['key']] = v
#                 
#             # Hack: activity.type is serviceactivity.type + activity.class
#             vocabularies['activity.type'] = deepcopy(vocabularies['serviceactivity.type'])
#             vocabularies['activity.type'].update(deepcopy(vocabularies['activity.class']))
#             
#             cache.set('vocabularies', vocabularies);
#         if scope in vocabularies:
#             return deepcopy(vocabularies[scope])
#         else:
#             logger.warn(str(('---unknown vocabulary scope:', scope)))
#             return {}
#     
#     def clear_cache(self):
#         super(VocabulariesResourceBak,self).clear_cache()
#         cache.delete('vocabularies');
# 
#     @un_cache
#     def put_list(self, request, **kwargs):
#         self.suppress_errors_on_bootstrap = True
#         result = super(VocabulariesResourceBak, self).put_list(request, **kwargs)
#         self.suppress_errors_on_bootstrap = False
#         return result
#     
#     @un_cache
#     def patch_list(self, request, **kwargs):
#         self.suppress_errors_on_bootstrap = True
#         result = super(VocabulariesResourceBak, self).patch_list(request, **kwargs)
#         self.suppress_errors_on_bootstrap = False
#         return result


class UserResource(ApiResource):

    def __init__(self, **kwargs):
        super(UserResource,self).__init__(**kwargs)
        
        self.permission_resource = None
        self.usergroup_resource = None

    class Meta:
        queryset = UserProfile.objects.all().order_by('username') 
        authentication = MultiAuthentication(
            BasicAuthentication(), SessionAuthentication())
        
        # FIXME: should override UserGroupAuthorization, and should allow user to view
        # (1) record by default: their own.
        authorization = SuperUserAuthorization()
        ordering = []
        filtering = {'scope':ALL, 'key': ALL, 'alias':ALL}
        serializer = LimsSerializer()
        excludes = [] #['json_field']
        always_return_data = True # this makes Backbone happy
        resource_name = 'user'
    
    def get_permission_resource(self):
        if not self.permission_resource:
            self.permission_resource = PermissionResource()
        return self.permission_resource
    
    def get_usergroup_resource(self):
        if not self.usergroup_resource:
            self.usergroup_resource = UserGroupResource()
        return self.usergroup_resource
    
    def prepend_urls(self):
        return [
            # override the parent "base_urls" so that we don't need to worry about schema again
            url(r"^(?P<resource_name>%s)/schema%s$" 
                % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('get_schema'), name="api_get_schema"),            
            
            url(r"^(?P<resource_name>%s)/(?P<username>([\w\d_]+))%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
            url(r"^(?P<resource_name>%s)/(?P<username>([\w\d_]+))/groups%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_user_groupview'), name="api_dispatch_user_groupview"),
            url(r"^(?P<resource_name>%s)/(?P<username>([\w\d_]+))/permissions%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_user_permissionview'), name="api_dispatch_user_permissionview"),
            ]    

    def dispatch_user_groupview(self, request, **kwargs):
        # signal to include extra column
        return UserGroupResource().dispatch('list', request, **kwargs)    
    
    def dispatch_user_permissionview(self, request, **kwargs):
        # signal to include extra column
        return PermissionResource().dispatch('list', request, **kwargs)    


    def build_schema(self):
        
        schema = super(UserResource,self).build_schema()
        try:
            if 'usergroups' in schema['fields']: # may be blank on initiation
                schema['fields']['usergroups']['choices'] = \
                    [x.name for x in UserGroup.objects.all()]
        except Exception, e:
            logger.exception('on get_schema')
            raise e  
        return schema

    def get_custom_columns(self):

        _up = self.bridge['reports_userprofile']
        _p = self.bridge['reports_permission']
        _ug = self.bridge['reports_usergroup']
        _upp = self.bridge['reports_userprofile_permissions']
        _ugu = self.bridge['reports_usergroup_users']
        
        # Create a recursive CTE to enumerate all groups/supergroups/subgroups
        group_all_supergroups = \
            UserGroupResource.recursive_supergroup_query(self.bridge)

        group_all_permissions = \
            UserGroupResource.recursive_permissions_query(self.bridge,group_all_supergroups)
        
        group_all_subgroups = \
            UserGroupResource.recursive_subgroups_query(self.bridge,group_all_supergroups)
            
        group_all_users = \
            UserGroupResource.recursive_group_all_users(self.bridge,group_all_subgroups)

        user_all_group_permissions = select([
            _ugu.c.userprofile_id,
            func.array_agg(distinct(_p.c.id)).label('all_permissions')]).\
            select_from(
                _ugu.join(group_all_permissions,_ugu.c.usergroup_id
                    ==group_all_permissions.c.usergroup_id)).\
            where(_p.c.id==text('any(gap.permission_ids)')).\
            group_by(_ugu.c.userprofile_id)
        user_all_group_permissions = user_all_group_permissions.cte('uagp') 
        
        user_all_permissions = user_all_group_permissions.union_all(
            select([_upp.c.userprofile_id, func.array_agg(_p.c.id)]).\
            select_from(_p.join(_upp,_upp.c.permission_id==_p.c.id)).\
            group_by(_upp.c.userprofile_id)).alias('uap')
        
        
        #             user_all_permissions = select([
        #                 user_all_group_permissions.c.userprofile_id,
        #                 user_all_group_permissions.c.all_permissions]).\
        #                 union(select([
        #                     _upp.c.userprofile_id,
        #                     text('array[reports_userprofile_permissions.permission_id]')]))
        #             user_all_permissions = user_all_permissions.cte('uap')
                         
        _ugu1=_ugu.alias('ugu1')
        _ugx = _ug.alias('ugx')
        custom_columns = {
            'resource_uri': func.array_to_string(array([
                BASE_URI,'user',text('reports_userprofile.username')]),'/'),
            'permissions': 
                select([func.array_to_string(
                        func.array_agg(text('inner_perms.permission')),
                        LIST_DELIMITER_SQL_ARRAY)]).\
                select_from(
                    select([func.array_to_string(array([
                            _p.c.scope,_p.c.key,_p.c.type]),'/').label('permission')
                        ]).\
                    select_from(_p.join(_upp,_p.c.id==_upp.c.permission_id)).\
                    where(text('reports_userprofile.id')==_upp.c.userprofile_id).\
                    order_by('permission').alias('inner_perms')),
            'usergroups': 
                select([func.array_to_string(
                        func.array_agg(text('inner_groups.name')), 
                        LIST_DELIMITER_SQL_ARRAY)]).\
                select_from(
                    select([_ugx.c.name]).\
                    select_from(_ugx.join(_ugu1,_ugx.c.id==_ugu1.c.usergroup_id)).\
                    where(_ugu1.c.userprofile_id==text('reports_userprofile.id')).\
                    order_by('name').alias('inner_groups')),
            'all_permissions':
                select([func.array_to_string(func.array_agg(
                    text('innerp.permission')),LIST_DELIMITER_SQL_ARRAY)]).\
                select_from(
                    select([func.array_to_string(array([
                            _p.c.scope,_p.c.key,_p.c.type]),'/').label('permission')
                        ]).\
                    select_from(user_all_permissions).\
                    where(and_(
                        user_all_permissions.c.userprofile_id==text('reports_userprofile.id'),
                        _p.c.id==text('any(uap.all_permissions)'))
                        ).\
                    order_by('permission').alias('innerp')),
            }

        return custom_columns

    def get_detail(self, request, **kwargs):
        logger.info(str(('get_detail')))

        username = kwargs.get('username', None)
        if not username:
            logger.info(str(('no username provided', kwargs)))
            raise NotImplementedError('must provide a username parameter')

        kwargs['visibilities'] = kwargs.get('visibilities', ['d'])
        kwargs['is_for_detail']=True
        return self.build_list_response(request, **kwargs)
        
    @read_authorization
    def get_list(self,request,**kwargs):

        kwargs['visibilities'] = kwargs.get('visibilities', ['l'])

        return self.build_list_response(request, **kwargs)

        
    def build_list_response(self,request, **kwargs):
        ''' 
        Overrides tastypie.resource.Resource.get_list for an SqlAlchemy implementation
        @returns django.http.response.StreamingHttpResponse 
        '''
        DEBUG_GET_LIST = False or logger.isEnabledFor(logging.DEBUG)

        param_hash = {}
        param_hash.update(kwargs)
        param_hash.update(self._convert_request_to_dict(request))
        
        is_for_detail = kwargs.pop('is_for_detail', False)

        schema = super(UserResource,self).build_schema()
        
        filename = self._get_filename(schema, kwargs)
        
        logger.info(str(('get_list', filename, kwargs)))
        
        username = param_hash.pop('username', None)
        if username:
            param_hash['username__eq'] = username

        groupname = param_hash.pop('groupname', None)
        if groupname:
            param_hash['usergroups__eq'] = groupname

        try:
            
            # general setup
             
            manual_field_includes = set(param_hash.get('includes', []))
            
            if DEBUG_GET_LIST: 
                logger.info(str(('manual_field_includes', manual_field_includes)))
  
            (filter_expression, filter_fields) = \
                SqlAlchemyResource.build_sqlalchemy_filters(schema, param_hash=param_hash)
                  
            field_hash = self.get_visible_fields(
                schema['fields'], filter_fields, manual_field_includes, 
                param_hash.get('visibilities'), 
                exact_fields=set(param_hash.get('exact_fields',[])))
              
            order_params = param_hash.get('order_by',[])
            order_params.append('username')
            order_clauses = \
                SqlAlchemyResource.build_sqlalchemy_ordering(order_params, field_hash)
             
            rowproxy_generator = None
            if param_hash.get(HTTP_PARAM_USE_VOCAB,False):
                rowproxy_generator = \
                    IccblBaseResource.create_vocabulary_rowproxy_generator(field_hash)
 
            # specific setup
            custom_columns = {
                'resource_uri': func.array_to_string(array([
                    BASE_URI,'user',text('auth_user.username')]),'/'),
            }
            columns = self.build_sqlalchemy_columns(field_hash.values(),custom_columns=custom_columns )

            # build the query statement
            _au = self.bridge['auth_user']
            _up = self.bridge['reports_userprofile']

            j = _up
            j = j.join(_au,_up.c.user_id==_au.c.id, isouter=True)
            stmt = select(columns.values()).select_from(j)

            # general setup
             
            (stmt,count_stmt) = self.wrap_statement(stmt,order_clauses,filter_expression )
            
            title_function = None
            if param_hash.get(HTTP_PARAM_USE_TITLES, False):
                title_function = lambda key: field_hash[key]['title']
            
            return self.stream_response_from_statement(
                request, stmt, count_stmt, filename, 
                field_hash=field_hash, 
                param_hash=param_hash,
                is_for_detail=is_for_detail,
                rowproxy_generator=rowproxy_generator,
                title_function=title_function  )
             
        except Exception, e:
            logger.exception('on get_list')
            raise e  

    # FIXME: deprecated
#     def get_resource_uri(self, bundle_or_obj=None, url_name='api_dispatch_list'):
#         ''' 
#         Override - Either have to generate localized resource uri, or, 
#         in client, equate localized uri with non-localized uri's (* see user.js,
#         where _.without() is used).
#         This modification represents the first choice
#         '''
#         return self.get_local_resource_uri(
#             bundle_or_obj=bundle_or_obj, url_name=url_name)

    def build_sqlalchemy_columns(self, fields, custom_columns=None ):
        
        if not custom_columns:
            custom_columns = {}
        custom_columns.update(self.get_custom_columns())
        base_query_tables = ['auth_user','reports_userprofile'] 

        return super(UserResource,self).build_sqlalchemy_columns(
            fields,base_query_tables=base_query_tables,custom_columns=custom_columns)
        
    def get_id(self, deserialized, **kwargs):
        id_kwargs = ApiResource.get_id(self, deserialized, **kwargs)
        if not id_kwargs:
            if deserialized and deserialized.get('ecommons_id', None):
                id_kwargs = { 'ecommons_id': deserialized['ecommons_id']}
            elif kwargs and kwargs.get('ecommons_id', None):
                id_kwargs = { 'ecommons_id': kwargs['ecommons_id']}
            else:
                raise ValueError, 'neither username or ecommons_id not specified: %r, %r' %(deserialized,kwargs)
        return id_kwargs
    
    @transaction.atomic()    
    def delete_obj(self, deserialized, **kwargs):
        id_kwargs = self.get_id(deserialized,**kwargs)
        UserProfile.objects.get(**id_kwargs).delete()
    
    @transaction.atomic()    
    def patch_obj(self,deserialized, **kwargs):

        logger.debug('patch_obj: %r, %r', deserialized,kwargs)
        
        id_kwargs = self.get_id(deserialized,**kwargs)
        username = id_kwargs.get('username', None)
        ecommons_id = id_kwargs.get('ecommons_id', None)
        
        schema = self.build_schema()
        fields = schema['fields']

        auth_user_fields = { name:val for name,val in fields.items() 
            if val['table'] and val['table']=='auth_user'}
        userprofile_fields = { name:val for name,val in fields.items() 
            if val['table'] and val['table']=='reports_userprofile'}
        
        try:
            # create the auth_user
            if not username:
                logger.info('username not specified, setting username to ecommons_id: %s', ecommons_id)
                username = ecommons_id
                deserialized['username'] = username
                
            try:
                user = DjangoUser.objects.get(username=username)
                errors = self.validate(deserialized, patch=True)
                if errors:
                    raise ValidationError(errors)
            except ObjectDoesNotExist, e:
                logger.info('User %s does not exist, creating', id_kwargs)
                errors = self.validate(deserialized, patch=False)
                if errors:
                    raise ValidationError(errors)
                user = DjangoUser.objects.create_user(username=username)
                logger.info('created Auth.User: %s', user)

            initializer_dict = {}
            for key in auth_user_fields.keys():
                if key in deserialized:
                    initializer_dict[key] = parse_val(
                        deserialized.get(key,None), key, 
                        auth_user_fields[key]['data_type']) 
            if initializer_dict:
                for key,val in initializer_dict.items():
                    if hasattr(user,key):
                        setattr(user,key,val)
                user.save()
                logger.info('== created/updated auth user: %r', user.username)
            else:
                logger.info('no auth_user fields to update %s', deserialized)
                
            # create the reports userprofile
            initializer_dict = {}
            for key in userprofile_fields.keys():
                if key in deserialized:
                    initializer_dict[key] = parse_val(
                        deserialized.get(key,None), key,
                        userprofile_fields[key]['data_type']) 

            userprofile = None
            try:
                userprofile = UserProfile.objects.get(**id_kwargs)
            except ObjectDoesNotExist, e:
                if hasattr(user, 'userprofile'):
                    raise ValueError('user already exists: %s: %s' % (user, user.userprofile))
                logger.info('Reports User %s does not exist, creating' % id_kwargs)
                userprofile = UserProfile.objects.create(**id_kwargs)
                logger.info('created UserProfile: %s', userprofile)
            
            userprofile.user = user
            userprofile.save()

            if initializer_dict:
                logger.info('initializer dict: %r', initializer_dict)
                for key,val in initializer_dict.items():
                    logger.debug('set: %s to %r, %s',key,val,hasattr(userprofile, key))
                    
                    if key == 'permissions':
                        # FIXME: first check if permissions have changed
                        userprofile.permissions.clear()
                        if val:
                            pr = self.get_permission_resource()
                            for p in val:
                                permission_key = ( 
                                    pr.find_key_from_resource_uri(p))
                                try:
                                    permission = Permission.objects.get(**permission_key)
                                    userprofile.permissions.add(permission)
                                except ObjectDoesNotExist, e:
                                    logger.warn(str(('no such permission', p, 
                                        permission_key, initializer_dict)))
                                    # if permission does not exist, create it
                                    # TODO: should be created through the permission resource
                                    permission = Permission.objects.create(**permission_key)
                                    permission.save()
                                    logger.info(str(('created permission', permission)))
                                    userprofile.permissions.add(permission)
                                    userprofile.save()
                    elif key == 'usergroups':
                        # FIXME: first check if groups have changed
                        logger.info(str(('process groups', val)))
                        userprofile.usergroup_set.clear()
                        if val:
                            ugr = self.get_usergroup_resource()
                            for g in val:
                                usergroup_key = ugr.find_key_from_resource_uri(g)
                                try:
                                    usergroup = UserGroup.objects.get(**usergroup_key)
                                    usergroup.users.add(userprofile)
                                    usergroup.save()
                                    logger.info(str(('added user to usergroup', userprofile,userprofile.user, usergroup)))
                                except ObjectDoesNotExist as e:
                                    msg = ('no such usergroup: %r, initializer: %r'
                                        % (usergroup_key, initializer_dict))
                                    logger.exception(msg)
                                    raise ValidationError(msg)
                    elif hasattr(userprofile,key):
                        setattr(userprofile,key,val)

                userprofile.save()
                logger.info(str(('== created/updated userprofile', user, user.username)))
            else:
                logger.info('no reports_userprofile fields to update %s', deserialized)

            return userprofile
            
        except Exception:
            logger.exception('on put_detail')
            raise  


# class UserGroupResource(ManagedSqlAlchemyResourceMixin):
class UserGroupResource(ApiResource):
    
    class Meta:
        queryset = UserGroup.objects.all();        
        
        authentication = MultiAuthentication(
            BasicAuthentication(), SessionAuthentication())
        authorization = UserGroupAuthorization() #SuperUserAuthorization()        

        ordering = []
        filtering = {}
        serializer = LimsSerializer()
        excludes = [] #['json_field']
        always_return_data = True # this makes Backbone happy
        resource_name='usergroup' 
    
    def __init__(self, **kwargs):
        super(UserGroupResource,self).__init__(**kwargs)
    
        self.permission_resource = None
        self.user_resource = None

    def get_permission_resource(self):
        if not self.permission_resource:
            self.permission_resource = PermissionResource()
        return self.permission_resource
    
    def get_user_resource(self):
        if not self.user_resource:
            self.user_resource = UserResource()
        return self.user_resource

    def find_name(self,deserialized, **kwargs):
        name = kwargs.get('name', None)
        if not name:
            name = deserialized.get('name', None)
        if not name and 'resource_uri' in deserialized:
            keys = self.find_key_from_resource_uri(deserialized['resource_uri'])
            name = keys.get('name', None)
        if not name:
            raise NotImplementedError(str((
                'must provide a group "name" parameter',
                kwargs, deserialized)) )
        return name
    
#     @un_cache        
#     def put_list(self,request, **kwargs):
# 
#         # TODO: refactor use decorator
#         self._meta.authorization._is_resource_authorized(
#             self._meta.resource_name, request.user, 'write')
# 
#         deserialized = self.deserialize(request,request.body)
#         if not self._meta.collection_name in deserialized:
#             raise BadRequest("Invalid data sent, must be nested in '%s'" 
#                 % self._meta.collection_name)
#         deserialized = deserialized[self._meta.collection_name]
#         
#         # cache state, for logging
#         response = self.get_list(
#             request,
#             desired_format='application/json',
#             includes='*',
#             **kwargs)
#         original_data = self._meta.serializer.deserialize(
#             LimsSerializer.get_content(response), format='application/json')
#         original_data = original_data[self._meta.collection_name]
#         logger.info(str(('original data', original_data)))
# 
#         with transaction.atomic():
#             
#             # TODO: review REST actions:
#             # PUT deletes the endpoint
#             
#             UserGroup.objects.all().delete()
#             
#             for _dict in deserialized:
#                 self.put_obj(_dict)
# 
#         # get new state, for logging
#         response = self.get_list(
#             request,
#             desired_format='application/json',
#             includes='*',
#             **kwargs)
#         new_data = self._meta.serializer.deserialize(
#             LimsSerializer.get_content(response), format='application/json')
#         new_data = new_data[self._meta.collection_name]
#         
#         logger.info(str(('new data', new_data)))
#         self.log_patches(request, original_data,new_data,**kwargs)
# 
#     
#         if not self._meta.always_return_data:
#             return http.HttpAccepted()
#         else:
#             response = self.get_list(request, **kwargs)             
#             response.status_code = 200
#             return response
        
#     @un_cache        
#     def patch_list(self, request, **kwargs):
# 
#         # TODO: refactor
#         self._meta.authorization._is_resource_authorized(
#             self._meta.resource_name, request.user, 'write')
# 
#         deserialized = self._meta.serializer.deserialize(
#             request.body, 
#             format=request.META.get('CONTENT_TYPE', 'application/json'))
#         if not self._meta.collection_name in deserialized:
#             raise BadRequest("Invalid data sent, must be nested in '%s'" 
#                 % self._meta.collection_name)
#         deserialized = deserialized[self._meta.collection_name]
# 
#         # cache state, for logging
#         response = self.get_list(
#             request,
#             desired_format='application/json',
#             includes='*',
#             **kwargs)
#         original_data = self._meta.serializer.deserialize(
#             LimsSerializer.get_content(response), format='application/json')
#         original_data = original_data[self._meta.collection_name]
#         logger.info(str(('original data', original_data)))
# 
#         with transaction.atomic():
#             
#             for _dict in deserialized:
#                 self.patch_obj(_dict)
#                 
#         # get new state, for logging
#         response = self.get_list(
#             request,
#             desired_format='application/json',
#             includes='*',
#             **kwargs)
#         new_data = self._meta.serializer.deserialize(
#             LimsSerializer.get_content(response), format='application/json')
#         new_data = new_data[self._meta.collection_name]
#         
#         logger.info(str(('new data', new_data)))
#         self.log_patches(request, original_data,new_data,**kwargs)
#         
#         if not self._meta.always_return_data:
#             return http.HttpAccepted()
#         else:
#             response = self.get_list(request, **kwargs)             
#             response.status_code = 200
#             return response

#     @un_cache        
#     def patch_detail(self, request, **kwargs):
#         logger.info(str(('patch detail', kwargs)))
#         
#         # TODO: refactor
#         self._meta.authorization._is_resource_authorized(
#             self._meta.resource_name, request.user, 'write')
#         
#         deserialized = self._meta.serializer.deserialize(
#             request.body, 
#             format=request.META.get('CONTENT_TYPE', 'application/json'))
#         
#         # cache state, for logging
#         response = self.get_list(
#             request,
#             desired_format='application/json',
#             includes='*',
#             **kwargs)
#         original_data = self._meta.serializer.deserialize(
#             LimsSerializer.get_content(response), format='application/json')
#         original_data = original_data[self._meta.collection_name]
#         logger.info(str(('original data', original_data)))
# 
#         with transaction.atomic():
#             logger.info(str(('patch_detail:', kwargs)))
#             
#             self.patch_obj(deserialized, **kwargs)
# 
#         # get new state, for logging
#         response = self.get_list(
#             request,
#             desired_format='application/json',
#             includes='*',
#             **kwargs)
#         new_data = self._meta.serializer.deserialize(
#             LimsSerializer.get_content(response), format='application/json')
#         new_data = new_data[self._meta.collection_name]
#         
#         logger.info(str(('new data', new_data)))
#         self.log_patches(request, original_data,new_data,**kwargs)
# 
#         
#         if not self._meta.always_return_data:
#             return http.HttpAccepted()
#         else:
#             response = self.get_detail(request, **kwargs) 
#             response.status_code = 200
#             return response

#     @un_cache        
#     def put_detail(self, request, **kwargs):
#         # TODO: refactor
#         self._meta.authorization._is_resource_authorized(
#             self._meta.resource_name, request.user, 'write')
#                 
#         deserialized = self._meta.serializer.deserialize(
#             request.body, 
#             format=request.META.get('CONTENT_TYPE', 'application/json'))
#         
#         with transaction.atomic():
#             logger.info(str(('put_detail:', kwargs)))
#             
#             self.put_obj(deserialized, **kwargs)
#         
#         if not self._meta.always_return_data:
#             return http.HttpAccepted()
#         else:
#             response = self.get_detail(request, **kwargs) 
#             response.status_code = 200
#             return response
        
    @transaction.atomic()    
    def put_obj(self,deserialized, **kwargs):
        
        try:
            self.delete_obj(deserialized, **kwargs)
        except ObjectDoesNotExist,e:
            pass 
        
        return self.patch_obj(deserialized, **kwargs)
    
    def delete_detail(self,deserialized, **kwargs):
        # TODO: refactor
        self._meta.authorization._is_resource_authorized(
            self._meta.resource_name, request.user, 'write')

        deserialized = self._meta.serializer.deserialize(
            request.body, 
            format=request.META.get('CONTENT_TYPE', 'application/json'))
        try:
            self.delete_obj(deserialized, **kwargs)
            return HttpResponse(status=204)
        except ObjectDoesNotExist,e:
            return HttpResponse(status=404)
    
    @transaction.atomic()    
    def delete_obj(self, deserialized, **kwargs):
        name = self.find_name(deserialized,**kwargs)
        UserGroup.objects.get(name=name).delete()
    
    @transaction.atomic()    
    def patch_obj(self,deserialized, **kwargs):

        name = self.find_name(deserialized,**kwargs)
        
        schema = self.build_schema()
        fields = schema['fields']

        group_fields = { name:val for name,val in fields.items() 
            if val['table'] and val['table']=='reports_usergroup'}
        logger.debug(str(('usergroup patch_obj', fields, deserialized)))
        try:
            # create the group

            initializer_dict = {}
            for key in fields.keys():
                if deserialized.get(key,None):
                    initializer_dict[key] = parse_val(
                        deserialized.get(key,None), key, 
                        fields[key]['data_type']) 

            usergroup = None
            try:
                usergroup = UserGroup.objects.get(name=name)
            except ObjectDoesNotExist, e:
                logger.info('Reports UserGroup %s does not exist, creating' % name)
                usergroup = UserGroup.objects.create(name=name)
                usergroup.save()
            
            logger.info(str(('initializer dict', initializer_dict)))
            for key,val in initializer_dict.items():
                logger.info(str(('set',key,val,usergroup, hasattr(usergroup, key))))
                
                if key == 'permissions':
                    usergroup.permissions.clear()
                    pr = self.get_permission_resource()
                    for p in val:
                        permission_key = ( 
                            pr.find_key_from_resource_uri(p))
                        try:
                            permission = Permission.objects.get(**permission_key)
                        except ObjectDoesNotExist, e:
                            logger.warn(str(('no such permission', p, 
                                permission_key, initializer_dict)))
                            # if permission does not exist, create it
                            # TODO: should be created through the permission resource
                            permission = Permission.objects.create(**permission_key)
                            permission.save()
                        usergroup.permissions.add(permission)
                        usergroup.save()
                        logger.info(str(('added permission to group', permission,usergroup)))
                elif key == 'users':
                    usergroup.users.clear()
                    ur = self.get_user_resource()
                    for u in val:
                        user_key = ur.find_key_from_resource_uri(u)
                        try:
                            user = UserProfile.objects.get(**user_key)
                            usergroup.users.add(user)
                            logger.info(str(('added user to group', user, usergroup)))
                        except ObjectDoesNotExist, e:
                            logger.info(str(('no such user', u, 
                                user_key, initializer_dict)))
                            raise e
                elif key == 'super_groups':
                    usergroup.super_groups.clear()
                    for ug in val:
                        ug_key = self.find_key_from_resource_uri(ug)
                        try:
                            supergroup = UserGroup.objects.get(**ug_key)
                            usergroup.super_groups.add(supergroup)
                            logger.info(str(('added usergroup',supergroup,'to usergroup',usergroup)))
                        except ObjectDoesNotExist, e:
                            logger.warn(str(('no such usergroup',ug_key,initializer_dict)))
                            raise e
                elif key == 'sub_groups':
                    usergroup.sub_groups.clear()
                    for ug in val:
                        ug_key = self.find_key_from_resource_uri(ug)
                        try:
                            subgroup = UserGroup.objects.get(**ug_key)
                            subgroup.super_groups.add(usergroup)
                            subgroup.save()
                            logger.info(str(('added subgroup to group', subgroup, usergroup)))
                        except ObjectDoesNotExist, e:
                            logger.warn(str(('no such usergroup',ug_key,initializer_dict)))
                            raise e
                elif key in group_fields and hasattr(usergroup,key):
                    setattr(usergroup,key,val)
                else:
                    logger.warn(str(('unknown attribute', key, val, usergroup,initializer_dict)))
            # also set
            usergroup.save()
            return usergroup
            
        except Exception, e:
            logger.exception('on put_detail')
            raise e  

        
    @staticmethod    
    def recursive_supergroup_query(bridge):
        '''
        Create a recursive CTE to enumerate all groups/supergroups.
        - For use in building sqlalchemy statements.
        columns: 
        - id: the usergroup id
        - name: the usergroup name
        - sg_ids: supergroup ids (recursive) for the usergroup
        @param bridge an instance of reports.utils.sqlalchemy_bridge.Bridge
        @return: an sqlalchemy statement
        WITH group_super_rpt as (
            WITH RECURSIVE group_supergroups(from_id, sg_ids, cycle) AS 
            (
                SELECT 
                    ugsg.from_usergroup_id,
                    array[ugsg.to_usergroup_id],
                    false as cycle
                from reports_usergroup_super_groups ugsg
                UNION ALL
                SELECT
                    sgs.from_usergroup_id,
                    sgs.to_usergroup_id || g_s.sg_ids as sg_ids,
                    sgs.from_usergroup_id = any(sg_ids)
                from reports_usergroup_super_groups sgs, group_supergroups g_s
                where sgs.to_usergroup_id=g_s.from_id
                and not cycle 
            )
            select 
                ug.id, ug.name,gs.* 
            from reports_usergroup ug 
            left join group_supergroups gs on gs.from_id=ug.id 
            order by name 
        )
        select ug1.id, ug1.name,
        (
            select array_agg(distinct(ug2.id)) 
            from reports_usergroup ug2, group_super_rpt
            where ug2.id=any(group_super_rpt.sg_ids) 
            and group_super_rpt.from_id=ug1.id) as sg_ids 
        from
        reports_usergroup ug1 order by name;
        '''
        
        #Note: using the postgres specific ARRAY and "any" operator
        try:
            _ug = bridge['reports_usergroup']
            _ugsg = bridge['reports_usergroup_super_groups']
    
            ugsg1 = _ugsg.alias('ugsg1')
            group_supergroups = \
                select([
                    ugsg1.c.from_usergroup_id.label('from_id'),
                    literal_column('array[ugsg1.to_usergroup_id]').label('sg_ids'),
                    literal_column('false').label('cycle')
                ]).\
                select_from(ugsg1).\
                cte('group_supergroups',recursive=True)
            
            gsg_alias = group_supergroups.alias('gsg')
            _ugsg_outer = _ugsg.alias('ugsg2')
            group_all_supergroups = gsg_alias.union_all(
                select([
                    _ugsg_outer.c.from_usergroup_id,
                    func.array_append(gsg_alias.c.sg_ids,_ugsg_outer.c.to_usergroup_id),
                    _ugsg_outer.c.from_usergroup_id==text('any(gsg.sg_ids)')
                    ]).\
                select_from(gsg_alias).\
                where(and_(
                    _ugsg_outer.c.to_usergroup_id==gsg_alias.c.from_id,
                    gsg_alias.c.cycle==False)))
            group_all_supergroups = group_all_supergroups.alias('gsg_union')
            
            # The query so far returns each path to a supergroup as a separate row,
            # so the following query will return one row of all supergroups per item
            _ug1 = _ug.alias('ug1')
            _ug2 = _ug.alias('ug2')
            group_supergroup_rpt = \
                select([
                    _ug2.c.id,
                    _ug2.c.name,
                    select([
                        func.array_agg(distinct(_ug1.c.id))]).\
                        select_from(group_all_supergroups).\
                        where(and_(
                            _ug1.c.id==text('any(gsg_union.sg_ids)'),
                            group_all_supergroups.c.from_id==_ug2.c.id)).label('sg_ids')
                    ]).\
                select_from(_ug2).\
                order_by(_ug2.c.name)
            return group_supergroup_rpt.cte('group_sg_rpt')
        except Exception, e:
            logger.exception('on recursive_supergroup_query construction')
            raise e  

    @staticmethod
    def recursive_permissions_query(bridge,group_all_supergroups):

        _ugp = bridge['reports_usergroup_permissions']
        
        group_all_permissions = select([
            group_all_supergroups.c.id.label('usergroup_id'),
            func.array_agg(_ugp.c.permission_id).label('permission_ids')]).\
            where(or_(
                _ugp.c.usergroup_id==group_all_supergroups.c.id,
                _ugp.c.usergroup_id==text('any(group_sg_rpt.sg_ids)'))).\
            group_by(group_all_supergroups.c.id)
        group_all_permissions = group_all_permissions.cte('gap')
        
        return group_all_permissions
    
    @staticmethod    
    def recursive_subgroups_query(bridge, group_all_supergroups):
        _ug = bridge['reports_usergroup']
        group_all_subgroups = \
            select([
                _ug.c.id,
                select([func.array_agg(group_all_supergroups.c.id)]).\
                    select_from(group_all_supergroups).\
                    where(_ug.c.id==text('any(group_sg_rpt.sg_ids)')).\
                label('subgroup_ids')
            ]).select_from(_ug)
        
        return group_all_subgroups.cte('gasubg')

    @staticmethod
    def recursive_group_all_users(bridge,group_all_subgroups):
        _up = bridge['reports_userprofile']
        _ugu = bridge['reports_usergroup_users']
        group_all_users = \
            select([
                group_all_subgroups.c.id,
                select([func.array_agg(_up.c.id)]).\
                    select_from(_up.join(_ugu,_up.c.id==_ugu.c.userprofile_id)).\
                    where(or_(
                        _ugu.c.usergroup_id==text('any(gasubg.subgroup_ids)'),
                        _ugu.c.usergroup_id==group_all_subgroups.c.id)).label('userprofile_ids')
                ]).\
            select_from(group_all_subgroups)

        return group_all_users.cte('gau')
    
    def get_detail(self, request, **kwargs):
        logger.info(str(('get_detail')))

        name = kwargs.get('name', None)
        if not name:
            logger.info(str(('no group name provided')))
            raise NotImplementedError('must provide a group name parameter')

        kwargs['visibilities'] = kwargs.get('visibilities', ['d'])
        kwargs['is_for_detail']=True
        return self.build_list_response(request, **kwargs)
        
    @read_authorization
    def get_list(self,request,**kwargs):

        kwargs['visibilities'] = kwargs.get('visibilities', ['l'])

        return self.build_list_response(request, **kwargs)

        
    def build_list_response(self,request, **kwargs):
        ''' 
        Overrides tastypie.resource.Resource.get_list for an SqlAlchemy implementation
        @returns django.http.response.StreamingHttpResponse 
        '''
        DEBUG_GET_LIST = False or logger.isEnabledFor(logging.DEBUG)

        param_hash = {}
        param_hash.update(kwargs)
        param_hash.update(self._convert_request_to_dict(request))
        
        is_for_detail = kwargs.pop('is_for_detail', False)

        schema = super(UserGroupResource,self).build_schema()

        filename = self._get_filename(schema, kwargs)
        
        logger.info(str(('get_list', filename, kwargs)))
        
        name = param_hash.pop('name', None)
        if name:
            param_hash['name__eq'] = name
        username = param_hash.pop('username', None)
        if username:
            param_hash['all_users__eq'] = username
        
        sub_group_name = param_hash.pop('sub_groupname',None)
        if sub_group_name:
            param_hash['all_sub_groups__eq']=sub_group_name
        
        super_groupname = param_hash.pop('super_groupname', None)
        if super_groupname:
            param_hash['all_super_groups__eq']=super_groupname    
        try:
            
            # general setup
          
            manual_field_includes = set(param_hash.get('includes', []))
            
            if DEBUG_GET_LIST: 
                logger.info(str(('manual_field_includes', manual_field_includes)))
  
            (filter_expression, filter_fields) = \
                SqlAlchemyResource.build_sqlalchemy_filters(schema, param_hash=param_hash)
                  
            field_hash = self.get_visible_fields(
                schema['fields'], filter_fields, manual_field_includes, 
                param_hash.get('visibilities'), 
                exact_fields=set(param_hash.get('exact_fields',[])))
              
            order_params = param_hash.get('order_by',[])
            order_clauses = SqlAlchemyResource.build_sqlalchemy_ordering(order_params, field_hash)
             
            rowproxy_generator = None
            if param_hash.get(HTTP_PARAM_USE_VOCAB,False):
                rowproxy_generator = IccblBaseResource.create_vocabulary_rowproxy_generator(field_hash)
            
            # specific setup
            _up = self.bridge['reports_userprofile']
            _p = self.bridge['reports_permission']
            _ug = self.bridge['reports_usergroup']
            _ugu = self.bridge['reports_usergroup_users']
            _ugp = self.bridge['reports_usergroup_permissions']
            _ugsg = self.bridge['reports_usergroup_super_groups']
            base_query_tables = ['reports_usergroup'] 
            
            # Create a recursive CTE to enumerate all groups/supergroups/subgroups
            group_all_supergroups = \
                UserGroupResource.recursive_supergroup_query(self.bridge)
            group_all_permissions = \
                UserGroupResource.recursive_permissions_query(self.bridge,group_all_supergroups)
            group_all_subgroups = \
                UserGroupResource.recursive_subgroups_query(self.bridge,group_all_supergroups)
            group_all_users = \
                UserGroupResource.recursive_group_all_users(self.bridge,group_all_subgroups)
                
            _ug1 = _ug.alias('ug1')
            _ug2 = _ug.alias('ug2')
            _ug3 = _ug.alias('ug3')
            custom_columns = {
                'resource_uri': func.array_to_string(array([
                    BASE_URI,'usergroup',text('reports_usergroup.name')]),'/'),
                'permissions': 
                    select([func.array_to_string(
                            func.array_agg(text('innerperm.permission')),
                            LIST_DELIMITER_SQL_ARRAY)]).\
                    select_from(
                        select([func.array_to_string(array([
                            _p.c.scope,_p.c.key,_p.c.type]),'/').\
                            label('permission')]).\
                        select_from(_p.join(_ugp,_p.c.id==_ugp.c.permission_id)).\
                        #FIXME: using "text" override to reference the outer reports_usergroup
                        # can be fixed by making an alias on the outer reports_usergroup
                        where(_ugp.c.usergroup_id==text('reports_usergroup.id')).\
                        order_by('permission').\
                        alias('innerperm')
                    ),
                'users': 
                    select([func.array_to_string(
                            func.array_agg(text('inner1.username')),
                            LIST_DELIMITER_SQL_ARRAY)]).\
                    select_from(
                        select([_up.c.username]).\
                        select_from(_up.join(_ugu,_up.c.id==_ugu.c.userprofile_id)).\
                        #FIXME: using "text" override to reference the outer reports_usergroup
                        # can be fixed by making an alias on the outer reports_usergroup
                        where(_ugu.c.usergroup_id==text('reports_usergroup.id')).\
                        order_by('username').\
                        alias('inner1')
                    ),
                'sub_groups': 
                    select([func.array_to_string(
                            func.array_agg(text('inner1.name')),
                            LIST_DELIMITER_SQL_ARRAY)]).\
                    select_from(
                        select([_ug2.c.name.label('name')]).\
                        select_from(_ug2.join(_ugsg,_ug2.c.id==_ugsg.c.from_usergroup_id)).\
                        #FIXME: using "text" override to reference the outer reports_usergroup
                        # can be fixed by making an alias on the outer reports_usergroup
                        where(_ugsg.c.to_usergroup_id==text('reports_usergroup.id')).\
                        order_by('name').\
                        alias('inner1')
                    ),
                'super_groups': 
                    select([func.array_to_string(
                            func.array_agg(text('inner1.name')),
                            LIST_DELIMITER_SQL_ARRAY)]).\
                    select_from(
                        select([_ug3.c.name.label('name')]).\
                        select_from(_ug3.join(_ugsg,_ug3.c.id==_ugsg.c.to_usergroup_id)).\
                        #FIXME: using "text" override to reference the outer reports_usergroup
                        # can be fixed by making an alias on the outer reports_usergroup
                        where(_ugsg.c.from_usergroup_id==text('reports_usergroup.id')).\
                        order_by('name').\
                        alias('inner1')
                    ),
                'all_permissions':
                    select([func.array_to_string(
                        func.array_agg(text('allperm.permission')),                            
                        LIST_DELIMITER_SQL_ARRAY)]).\
                    select_from(
                        select([func.array_to_string(array([
                            _p.c.scope,_p.c.key,_p.c.type]),'/').\
                            label('permission'),
                            group_all_permissions.c.usergroup_id ]).\
                        select_from(group_all_permissions).\
                        where(_p.c.id==text('any(gap.permission_ids)')).\
                        where(group_all_permissions.c.usergroup_id==\
                            text('reports_usergroup.id')).\
                        order_by('permission').\
                        alias('allperm')),
                'all_super_groups': 
                    select([func.array_to_string(
                        func.array_agg(text('supergroup.name')),
                        LIST_DELIMITER_SQL_ARRAY)]).\
                    select_from(
                        select([_ug1.c.name]).\
                        select_from(group_all_supergroups).\
                        where(and_(_ug1.c.id==text('any(group_sg_rpt.sg_ids)'),
                            group_all_supergroups.c.id==text('reports_usergroup.id'))).\
                        order_by(_ug1.c.name).alias('supergroup')),
                'all_sub_groups': 
                    select([func.array_to_string(
                        func.array_agg(text('subgroup.name')),
                        LIST_DELIMITER_SQL_ARRAY)]).\
                    select_from(
                        select([group_all_supergroups.c.name]).\
                        select_from(group_all_supergroups).\
                        where(text('reports_usergroup.id=any(group_sg_rpt.sg_ids)')).\
                        order_by(group_all_supergroups.c.name).alias('subgroup')
                    ),
                'all_users':
                    select([func.array_to_string(
                        func.array_agg(text('inneruser.username')),
                            LIST_DELIMITER_SQL_ARRAY)]).\
                    select_from( 
                        select([_up.c.username]).\
                        select_from(group_all_users).\
                        where(_up.c.id==text('any(gau.userprofile_ids)')).\
                        where(group_all_users.c.id==text('reports_usergroup.id')).\
                        order_by(_up.c.username).alias('inneruser')),
                }

            columns = self.build_sqlalchemy_columns(
                field_hash.values(), base_query_tables=base_query_tables,
                custom_columns=custom_columns )

            # build the query statement
            
            j = _ug
            stmt = select(columns.values()).select_from(j)
            stmt = stmt.order_by('name')
            # general setup
             
            (stmt,count_stmt) = self.wrap_statement(stmt,order_clauses,filter_expression )
            
            title_function = None
            if param_hash.get(HTTP_PARAM_USE_TITLES, False):
                title_function = lambda key: field_hash[key]['title']
            
            return self.stream_response_from_statement(
                request, stmt, count_stmt, filename, 
                field_hash=field_hash, 
                param_hash=param_hash,
                is_for_detail=is_for_detail,
                rowproxy_generator=rowproxy_generator,
                title_function=title_function  )
             
        except Exception, e:
            logger.exception('on get_list')
            raise e  
        
    def prepend_urls(self):
        return [
            # override the parent "base_urls" so that we don't need to worry about schema again
            url(r"^(?P<resource_name>%s)/schema%s$" 
                % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('get_schema'), name="api_get_schema"),            
            
            url(r"^(?P<resource_name>%s)/(?P<id>[\d]+)%s$" 
                    % (self._meta.resource_name, trailing_slash()),
                self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
            url(r"^(?P<resource_name>%s)/(?P<name>[^/]+)%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
            url(r"^(?P<resource_name>%s)/(?P<name>[^/]+)/users%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_group_userview'), name="api_dispatch_group_userview"),
            url(r"^(?P<resource_name>%s)/(?P<name>[^/]+)/permissions%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_group_permissionview'), 
                name="api_dispatch_group_permissionview"),
            url(r"^(?P<resource_name>%s)/(?P<name>[^/]+)/supergroups%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_group_supergroupview'), 
                name="api_dispatch_group_supergroupview"),
            url(r"^(?P<resource_name>%s)/(?P<name>[^/]+)/subgroups%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_group_subgroupview'), 
                name="api_dispatch_group_subgroupview"),
            ]

    def dispatch_group_userview(self, request, **kwargs):
        # signal to include extra column
        kwargs['groupname'] = kwargs.pop('name')  
        return UserResource().dispatch('list', request, **kwargs)    
    
    def dispatch_group_permissionview(self, request, **kwargs):
        # signal to include extra column
        kwargs['groupname'] = kwargs.pop('name')  
        return PermissionResource().dispatch('list', request, **kwargs)       
   
    def dispatch_group_supergroupview(self, request, **kwargs):
        # signal to include extra column
        kwargs['sub_groupname'] = kwargs.pop('name')  
        return self.dispatch('list', request, **kwargs)       

    def dispatch_group_subgroupview(self, request, **kwargs):
        # signal to include extra column
        kwargs['super_groupname'] = kwargs.pop('name')  
        return self.dispatch('list', request, **kwargs)    

#     def get_resource_uri(self, bundle_or_obj=None, url_name='api_dispatch_list'):
#         '''Override to shorten the URI'''
#         return self.get_local_resource_uri(
#             bundle_or_obj=bundle_or_obj, url_name=url_name)


class PermissionResource(ApiResource):

    class Meta:
        # note: the queryset for this resource is actually the permissions
        queryset = Permission.objects.all().order_by('scope', 'key')
        authentication = MultiAuthentication(
            BasicAuthentication(), SessionAuthentication())
        authorization= UserGroupAuthorization() #SuperUserAuthorization()        
        object_class = object
        
        ordering = []
        filtering = {}
        serializer = LimsSerializer()
        excludes = [] #['json_field']
        includes = [] 
        always_return_data = True # this makes Backbone happy
        resource_name='permission' 
    
    def __init__(self, **kwargs):
        super(PermissionResource,self).__init__(**kwargs)
        
        # create all of the permissions on startup
        resources = MetaHash.objects.filter(
            Q(scope='resource')|Q(scope__contains='fields.'))
        query = self._meta.queryset._clone()
        permissionTypes = Vocabularies.objects.all().filter(
            scope='permission.type')
        for r in resources:
            found = False
            for perm in query:
                if perm.scope==r.scope and perm.key==r.key:
                    found = True
            if not found:
                logger.info('initialize permission: %r:%r'
                    % (r.scope, r.key))
                for ptype in permissionTypes:
                    p = Permission.objects.create(
                        scope=r.scope, key=r.key, type=ptype.key)
                    logger.info('bootstrap created permission %s' % p)

    def prepend_urls(self):
        return [
            url(r"^(?P<resource_name>%s)/(?P<id>[\d]+)%s$" 
                    % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
            url((r"^(?P<resource_name>%s)/(?P<scope>[\w\d_.\-:]+)/"
                 r"(?P<key>[\w\d_.\-\+:]+)/(?P<type>[\w\d_.\-\+:]+)%s$" ) 
                        % (self._meta.resource_name, trailing_slash()), 
                self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
            ]
    
    def get_detail(self, request, **kwargs):

        scope = kwargs.get('scope', None)
        if not scope:
            logger.info('no scope provided')
            raise NotImplementedError('must provide a scope parameter')
        key = kwargs.get('key', None)
        if not key:
            logger.info('no key provided')
            raise NotImplementedError('must provide a key parameter')
        kwargs['visibilities'] = kwargs.get('visibilities', ['d'])
        kwargs['is_for_detail']=True
        return self.build_list_response(request, **kwargs)
        
    def get_list(self,request,**kwargs):

        kwargs['visibilities'] = kwargs.get('visibilities', ['l'])
        return self.build_list_response(request, **kwargs)

    @read_authorization
    def build_list_response(self,request, **kwargs):

        param_hash = {}
        param_hash.update(kwargs)
        param_hash.update(self._convert_request_to_dict(request))
        schema = self.build_schema()
        
        is_for_detail = kwargs.pop('is_for_detail', False)
        filename = self._get_filename(schema, kwargs)
        scope = param_hash.pop('scope', None)
        if scope:
            param_hash['scope__eq'] = scope
        key = param_hash.pop('key', None)
        if key:
            param_hash['key__eq'] = key
        
        try:
            
            # general setup
          
            manual_field_includes = set(param_hash.get('includes', []))
            
            (filter_expression, filter_fields) = \
                SqlAlchemyResource.build_sqlalchemy_filters(schema, param_hash=param_hash)
                  
            field_hash = self.get_visible_fields(
                schema['fields'], filter_fields, manual_field_includes, 
                param_hash.get('visibilities'), 
                exact_fields=set(param_hash.get('exact_fields',[])))
              
            order_params = param_hash.get('order_by',[])
            order_clauses = SqlAlchemyResource.build_sqlalchemy_ordering(
                order_params, field_hash)
             
            rowproxy_generator = None
            if param_hash.get(HTTP_PARAM_USE_VOCAB,False):
                rowproxy_generator = IccblBaseResource.\
                    create_vocabulary_rowproxy_generator(field_hash)
 
            # specific setup
            _p = self.bridge['reports_permission']
            _up = self.bridge['reports_userprofile']
            _upp = self.bridge['reports_userprofile_permissions']
            _ug = self.bridge['reports_usergroup']
            _ugp = self.bridge['reports_usergroup_permissions']
            
            custom_columns = {
                'users':
                    select([func.array_to_string(
                            func.array_agg(_up.c.username),LIST_DELIMITER_SQL_ARRAY)]).\
                        select_from(_up.join(_upp,_up.c.id==_upp.c.userprofile_id)).\
                        where(_upp.c.permission_id==_p.c.id),
                'groups':
                    select([func.array_to_string(
                            func.array_agg(_ug.c.name),LIST_DELIMITER_SQL_ARRAY)]).\
                        select_from(_ug.join(_ugp,_ug.c.id==_ugp.c.usergroup_id)).\
                        where(_ugp.c.permission_id==_p.c.id),
                'usergroups':
                    select([func.array_to_string(
                            func.array_agg(
                                _concat('usergroup/',_ug.c.name)),LIST_DELIMITER_SQL_ARRAY)]).\
                        select_from(_ug.join(_ugp,_ug.c.id==_ugp.c.usergroup_id)).\
                        where(_ugp.c.permission_id==_p.c.id),
                'resource_uri':
                    _concat('permission/',_p.c.scope,'/',_p.c.key,'/',_p.c.type),
                        
            }

            base_query_tables = ['reports_permission'] 
            columns = self.build_sqlalchemy_columns(
                field_hash.values(), base_query_tables=base_query_tables,
                custom_columns=custom_columns )

            j = _p
            stmt = select(columns.values()).select_from(j)
            # general setup
             
            (stmt,count_stmt) = self.wrap_statement(stmt,order_clauses,filter_expression )
            
            title_function = None
            if param_hash.get(HTTP_PARAM_USE_TITLES, False):
                title_function = lambda key: field_hash[key]['title']
            
            return self.stream_response_from_statement(
                request, stmt, count_stmt, filename, 
                field_hash=field_hash, 
                param_hash=param_hash,
                is_for_detail=is_for_detail,
                rowproxy_generator=rowproxy_generator,
                title_function=title_function  )
             
        except Exception, e:
            logger.exception('on get list')
            raise e  

    def delete_obj(self, deserialized, **kwargs):
        raise NotImplementedError('delete obj is not implemented for Permission')
    
    def patch_obj(self,deserialized, **kwargs):
        raise NotImplementedError('patch obj is not implemented for Permission')
    
    
    
# class PermissionResourceOld(ManagedModelResource):
#     
#     usergroups = fields.ToManyField(
#         'reports.api.UserGroupResource', 'usergroup_set', 
#         related_name='permissions', blank=True, null=True)
#     users = fields.ToManyField(
#         'reports.api.UserResource', 'userprofile_set', 
#         related_name='permissions', blank=True, null=True)
#     
#     groups = fields.CharField(attribute='groups', blank=True, null=True)
# 
#     is_for_group = fields.BooleanField(
#         attribute='is_for_group', blank=True, null=True)
#     is_for_user = fields.BooleanField(
#         attribute='is_for_user', blank=True, null=True)
# 
#     class Meta:
#         # note: the queryset for this resource is actually the permissions
#         queryset = Permission.objects.all().order_by('scope', 'key')
#         authentication = MultiAuthentication(
#             BasicAuthentication(), SessionAuthentication())
#         authorization= UserGroupAuthorization() #SuperUserAuthorization()        
#         object_class = object
#         
#         ordering = []
#         filtering = {}
#         serializer = LimsSerializer()
#         excludes = [] #['json_field']
#         includes = [] 
#         always_return_data = True # this makes Backbone happy
#         resource_name='permission' 
#     
#     def __init__(self, **kwargs):
#         super(PermissionResource,self).__init__(**kwargs)
#         
#         # create all of the permissions on startup
#         resources = MetaHash.objects.filter(
#             Q(scope='resource')|Q(scope__contains='fields.'))
#         query = self._meta.queryset._clone()
#         permissionTypes = Vocabularies.objects.all().filter(
#             scope='permission.type')
#         for r in resources:
#             found = False
#             for perm in query:
#                 if perm.scope==r.scope and perm.key==r.key:
#                     found = True
#             if not found:
#                 logger.debug('initialize permission: %r:%r'
#                     % (r.scope, r.key))
#                 for ptype in permissionTypes:
#                     p = Permission.objects.create(
#                         scope=r.scope, key=r.key, type=ptype.key)
#                     logger.debug('bootstrap created permission %s' % p)
# 
#     def prepend_urls(self):
#         return [
#             url(r"^(?P<resource_name>%s)/(?P<id>[\d]+)%s$" 
#                     % (self._meta.resource_name, trailing_slash()), 
#                 self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
#             url((r"^(?P<resource_name>%s)/(?P<scope>[\w\d_.\-:]+)/"
#                  r"(?P<key>[\w\d_.\-\+:]+)/(?P<type>[\w\d_.\-\+:]+)%s$" ) 
#                         % (self._meta.resource_name, trailing_slash()), 
#                 self.wrap_view('dispatch_detail'), name="api_dispatch_detail"),
#             ]
#     
#     def get_object_list(self, request, **kwargs): #username=None, groupname=None):
#         ''' 
#         Called immediately before filtering, actually grabs the (ModelResource) 
#         query - 
#         Override this and apply_filters, so that we can control the 
#         extra column "is_for_group":
#         This extra column is present when navigating to permissions from a 
#         usergroup; see prepend_urls.
#         TODO: we could programmatically create the "is_for_group" column by 
#         grabbing the entire queryset, converting to an array of dicts, and 
#         adding this field    
#         '''
#         query = super(PermissionResource, self).get_object_list(request);
#         if 'groupname' in kwargs:
#             groupname = kwargs.pop('groupname')
#             logger.info(str(('get_obj_list', groupname)))
#             query = query.extra(select = {
#                 'is_for_group': (
#                     '( select count(*)>0 '
#                     '  from reports_usergroup ug '
#                     '  join reports_usergroup_permissions rup '
#                        '  on(ug.id=rup.usergroup_id) '
#                     ' where rup.permission_id=reports_permission.id '
#                     ' and ug.name = %s )' ),
#               },
#               select_params = [groupname] )
#             query = query.order_by('-is_for_group')
#         if 'username' in kwargs:
#             username = kwargs.pop('username')
#             query = query.extra(select = {
#                 'is_for_user': (
#                     '( select count(*)>0 '
#                     '  from reports_userprofile up '
#                     '  join reports_userprofile_permissions rup '
#                        '  on(up.id=rup.userprofile_id) '
#                     ' where rup.permission_id=reports_permission.id '
#                     ' and up.username = %s )' ),
#               },
#               select_params = [username] )
#             query = query.order_by('-is_for_user')
#         return query
#     
#     def apply_filters(self, request, applicable_filters, **kwargs):
#         
#         query = self.get_object_list(request, **kwargs)
#         logger.info(str(('applicable_filters', applicable_filters)))
#         filters = applicable_filters.get('filter')
#         if filters:
#             
#             # Grab the groups/users filter out of the dict
#             groups_filter_val = None
#             users_filter_val = None
#             for f in filters.keys():
#                 if 'groups' in f:
#                     groups_filter_val = filters.pop(f)
#                 if 'userprofile' in f:
#                     users_filter_val = filters.pop(f)
# 
#             query = query.filter(**filters)
#             
#             # then add the groups filter back in
#             if groups_filter_val:
#                 ids = [x.id for x in Permission.objects.filter(
#                         usergroup__name__iexact=groups_filter_val)]
#                 query = query.filter(id__in=ids)
#             if users_filter_val:
#                 ids = [x.id for x in Permission.objects.filter(
#                         userprofile__username__iexact=users_filter_val)]
#                 query = query.filter(id__in=ids)
#             
#         e = applicable_filters.get('exclude')
#         if e:
#             groups_filter_val = None
#             users_filter_val = None
#             for x in e.keys():
#                 if 'userprofile' in x:
#                     users_filter_val = e.pop(x)
#                 if 'groups' in x:
#                     groups_filter_val = e.pop(x)
#             for exclusion_filter, value in e.items():
#                 query = query.exclude(**{exclusion_filter: value})
# 
#             # then add the user/groups filter back in
#             if groups_filter_val:
#                 ids = [x.id for x in Permission.objects.filter(
#                         usergroup__name__iexact=groups_filter_val)]
#                 query = query.exclude(id__in=ids)
#             if users_filter_val:
#                 ids = [x.id for x in Permission.objects.filter(
#                         userprofile__username__iexact=users_filter_val)]
#                 query = query.exclude(id__in=ids)
# 
#         return query         
# 
#     def apply_sorting(self, obj_list, options):
#         options = options.copy()
#         # Override to exclude this field in the PostgresSortingResource 
#         options['non_null_fields'] = ['groups','is_for_group','users','is_for_user'] 
#         obj_list = super(PermissionResource, self).apply_sorting(
#             obj_list, options)
#         return obj_list
# 
#     def get_resource_uri(self, bundle_or_obj=None, url_name='api_dispatch_list'):
#         return self.get_local_resource_uri(
#             bundle_or_obj=bundle_or_obj, url_name=url_name)
#     
#     def obj_get(self, bundle, **kwargs):
#         ''' 
#         basically, if a permission is requested that does not exist, 
#         it is created
#         '''
#         try:
# #             logger.info(str(('lookup with kwargs', kwargs)))
#             return super(PermissionResource, self).obj_get(bundle, **kwargs)
#         except ObjectDoesNotExist:
#             logger.info(str(('create permission on the fly', kwargs)))
#             p = Permission(**kwargs)
#             p.save()
#             return p
#     
#     def build_schema(self):
#         schema = super(PermissionResource,self).build_schema()
#         temp = [ x.scope for x in self.Meta.queryset.distinct('scope')]
#         schema['extraSelectorOptions'] = { 
#             'label': 'Resource', 'searchColumn': 'scope', 'options': temp }
#         return schema
        

KEY_QUERY_ALIAS_PATTERN = '_{key}'

# class ManagedLinkedResource(ManagedModelResource):
#     ''' store resource virtual fields in a related table
#     '''
# 
#     def __init__(self, **kwargs):
#         super(ManagedLinkedResource,self).__init__(**kwargs)
#         self.linked_field_defs = None
#             
#     def get_linked_fields(self, scope=None):
#         '''
#         Generate the resource fields that will be stored in the linked table
#         '''
#         if not self.linked_field_defs:
#             
#             schema = self.build_schema()
#             _fields = schema['fields']
#             resource = schema['resource_definition']
#             
#             self.linked_field_defs = { x: _fields[x] 
#                 for x,y in _fields.items() 
#                     if y.get('linked_field_value_field',None) }
# 
#             logger.debug(str(('lookup the module.model for each linked field', 
#                 self.linked_field_defs.keys() )))
#             for key,field_def in self.linked_field_defs.items():
#                 
#                 # Turn off dehydration for any of these fields that correspond 
#                 # to the automatic ModelResource fields
#                 if key in self.fields:
#                     self.fields[key].use_in = None
#                 
#                 linked_field_module = field_def.get('linked_field_module', None)
#                 if not linked_field_module:
#                     linked_field_module = resource.get('linked_table_module', None)
#                 if not linked_field_module:
#                     raise Exception(str((
#                         'no "linked_field_module" found in the field def', 
#                         field_def, 
#                         'no "linked_table_module" found in the resource def', 
#                         resource)))
#                     
#                 if '.' in linked_field_module:
#                     # Try to import.
#                     module_bits = linked_field_module.split('.')
#                     module_path, class_name = '.'.join(module_bits[:-1]), module_bits[-1]
#                     module = importlib.import_module(module_path)
#                 else:
#                     # We've got a bare class name here, which won't work (No AppCache
#                     # to rely on). Try to throw a useful error.
#                     raise ImportError(
#                         "linked_field_module requires a Python-style path "
#                         "(<module.module.Class>) to lazy load related resources. "
#                         "Only given '%s'." % linked_field_module )
#         
#                 module_class = getattr(module, class_name, None)
#         
#                 if module_class is None:
#                     raise ImportError(
#                         "Module '%s' does not appear to have a class called '%s'."
#                              % (module_path, class_name))
#                 else:
#                     field_def['linked_field_model'] = module_class
#                     field_def['meta_field_instance'] = \
#                             MetaHash.objects.get(key=field_def['key'])
# 
#         return self.linked_field_defs
#     
#     @log_obj_create
#     @transaction.atomic()
#     def obj_create(self, bundle, **kwargs):
#         
#         bundle.obj = self._meta.object_class()
# 
#         for key, value in kwargs.items():
#             setattr(bundle.obj, key, value)
# 
#         bundle = self.full_hydrate(bundle)
#         
#         # TODO: == make sure follows is in a transaction block
#         ## OK if called in "patch list"; not in "put list", TP's implementation of
#         ## "put list" has a "rollback" function instead of a tx block; so they
#         ## are implementing their own tx: see:
#         ## "Attempt to be transactional, deleting any previously created
#         ##  objects if validation fails."
#         
#         bundle = self.save(bundle)
# 
#         logger.debug(str(('==== save_linked_fields', self.get_linked_fields().keys() )))
#         
#         simple_linked_fields = {
#             k:v for (k,v) in self.get_linked_fields().items() if v.get('linked_field_module',None)}
#         for key,item in simple_linked_fields.items():
#             linkedModel = item.get('linked_field_model')
#             val = bundle.data.get(key,None)
#             field = self.fields[key]
#             if val:
#                 val = self._safe_get_field_val(key,field, val)
#                 if item['linked_field_type'] != 'fields.ListField':
#                     linkedObj = linkedModel()
#                     self._set_value_field(linkedObj, bundle.obj, item, val)
#                 else:
#                     self._set_multivalue_field(linkedModel, bundle.obj, item, val)
# 
#         # complex fields: 
#         # TODO: using a blank in 'linked_field_module' to indicate, this is abstruse
#         complex_linked_fields = {
#             k:v for (k,v) in self.get_linked_fields().items() if not v.get('linked_field_module',None)}
#         if len(complex_linked_fields):
#             # setup the linked model instance: some magic here - grab the model
#             # from the *first* field, since -all- the complex fields have the same one
#             linkedModel = complex_linked_fields.values()[0]['linked_field_model']
#             linkedObj = linkedModel()
#             setattr( linkedObj, 
#                 complex_linked_fields.values()[0]['linked_field_parent'], bundle.obj)
#             
#             for key,item in complex_linked_fields.items():
#                 val = bundle.data.get(key,None)
#                 field = self.fields[key]
#                 if val:
#                     val = self._safe_get_field_val(key,field, val)
#                     setattr( linkedObj, item['linked_field_value_field'], val)
#             linkedObj.save()
#                 
#         
#         return bundle
#     
#     def _safe_get_field_val(self, key,field, val):
#         try:
#             if hasattr(val, "strip"): # test if it is a string
#                 val = smart_text(val,'utf-8', errors='ignore')
#                 if isinstance( field, fields.ListField): 
#                     val = (val,)
#                 val = field.convert(val)
#             # test if it is a sequence - only string lists are supported
#             elif hasattr(val, "__getitem__") or hasattr(val, "__iter__"): 
#                 val = [smart_text(x,'utf-8',errors='ignore') for x in val]
#             return val
#         except Exception, e:
#             logger.exception('failed to convert %s with value "%s"' % (key,val))
#             raise e
# 
#     def _set_value_field(self, linkedObj, parent, item, val):
#         ## TODO: updates should be able to set fields to None
#         
#         setattr( linkedObj, item['linked_field_parent'], parent)
#         
#         if item.get('linked_field_meta_field', None):
#             setattr( linkedObj,item['linked_field_meta_field'], item['meta_field_instance'])
#         
#         setattr( linkedObj, item['linked_field_value_field'], val)
#         linkedObj.save()
# 
#     def _set_multivalue_field(self, linkedModel, parent, item, val):
#         logger.info(str(('_set_multivalue_field', item['key'], linkedModel, parent, item, val)))
#         if isinstance(val, six.string_types):
#             val = (val) 
#         for i,entry in enumerate(val):
#             linkedObj = linkedModel()
#             setattr( linkedObj, item['linked_field_parent'], parent)
#             if item.get('linked_field_meta_field', None):
#                 setattr( linkedObj,item['linked_field_meta_field'], item['meta_field_instance'])
#             setattr( linkedObj, item['linked_field_value_field'], entry)
#             if hasattr(linkedObj, 'ordinal'):
#                 linkedObj.ordinal = i
#             linkedObj.save()
#     
#     @log_obj_update
#     def obj_update(self, bundle, skip_errors=False, **kwargs):
#         """
#         A linked_field specific version
#         """
#         bundle = self._locate_obj(bundle)
#         
#         bundle = self.full_hydrate(bundle)
#         self.is_valid(bundle)
#         if bundle.errors and not skip_errors:
#             raise ImmediateHttpResponse(response=self.error_response(bundle.request, bundle.errors))
# 
#         bundle.obj.save()
#         
#         logger.info(str(('==== update_linked_fields', self.get_linked_fields().keys() )))
# 
#         simple_linked_fields = {
#             k:v for (k,v) in self.get_linked_fields().items() if v.get('linked_field_module',None)}
#         for key,item in simple_linked_fields.items():
#             val = bundle.data.get(key,None)
#             field = self.fields[key]
#             
#             if val:
#                 val = self._safe_get_field_val(key,field, val)
#                 linkedModel = item.get('linked_field_model')
# 
#                 params = { item['linked_field_parent']: bundle.obj }
#                 if item.get('linked_field_meta_field', None):
#                     params[item['linked_field_meta_field']] = item['meta_field_instance']
# 
#                 if item['linked_field_type'] != 'fields.ListField':
#                     linkedObj = None
#                     try: 
#                         linkedObj = linkedModel.objects.get(**params)
#                     except ObjectDoesNotExist:
#                         logger.warn(str((
#                             'update, could not find extant linked field for', 
#                             bundle.obj, item['meta_field_instance'])))
#                         linkedObj = linkedModel()
#                     
#                     self._set_value_field(linkedObj, bundle.obj, item, val)
#                 else:
#                     query = linkedModel.objects.filter( **params ) #.order_by('ordinal')
#                     values = query.values_list(
#                             item['linked_field_value_field'], flat=True)
#                     if values == val:
#                         pass
#                     else:
#                         query.delete()
#                     self._set_multivalue_field(linkedModel, bundle.obj, item, val)
#         
#         # complex fields: 
#         # TODO: using a blank in 'linked_field_module' to indicate, this is abstruse
#         complex_linked_fields = {
#             k:v for (k,v) in self.get_linked_fields().items() if not v.get('linked_field_module',None)}
#         if len(complex_linked_fields):
#             # setup the linked model instance: some magic here - grab the model
#             # from the *first* field, since -all- the complex fields have the same one
#             linkedModel = complex_linked_fields.values()[0]['linked_field_model']
#             linkedObj = None
#             try: 
#                 linkedObj = linkedModel.objects.get(**{ 
#                     item['linked_field_parent']: bundle.obj })
#             except ObjectDoesNotExist:
#                 logger.warn(str((
#                     'update, could not find extant linked complex module for', 
#                     bundle.obj)))
#                 linkedObj = linkedModel()
#                 setattr( linkedObj, item['linked_field_parent'], bundle.obj)
#             
#             for key,item in complex_linked_fields.items():
#                 val = bundle.data.get(key,None)
#                 field = self.fields[key]
#                 if val:
#                     val = self._safe_get_field_val(key,field, val)
#                     setattr( linkedObj, item['linked_field_value_field'], val)
#             linkedObj.save()
#         
#         return bundle
#                 
#     @log_obj_delete
#     def obj_delete(self, bundle, **kwargs):
#         if not hasattr(bundle.obj, 'delete'):
#             try:
#                 bundle.obj = self.obj_get(bundle=bundle, **kwargs)
#             except ObjectDoesNotExist:
#                 raise NotFound("A model instance matching the provided arguments could not be found.")
# 
#         self.authorized_delete_detail(self.get_object_list(bundle.request), bundle)
# 
#         # TODO: TEST
#         logger.info(str(('==== delete_linked_fields', self.get_linked_fields().keys() )))
#         linkedModel = item.get('linked_field_model')
#         linkedModel.objects.filter(**{
#             item['linked_field_parent']: bundle.obj }).delete()
#         bundle.obj.delete()
# 
#     def full_dehydrate(self, bundle, for_list=False):
#         # trigger get_linked_fields to turn off "use_in" for model fields
#         self.get_linked_fields()
#         bundle =  ManagedModelResource.full_dehydrate(self, bundle, for_list=for_list)
#         return bundle
#     
#     def get_object_list(self, request):
#         query = super(ManagedLinkedResource,self).get_object_list(request)
#         
#         # FIXME: SQL injection attack through metadata.  (at least to avoid inadvertant actions)
#         # TODO: use SqlAlchemy http://docs.sqlalchemy.org/en/latest/core/expression_api.html
#         extra_select = OrderedDict()
#         # NOTE extra_tables cannot be used, because it creates an inner join
#         #         extra_tables = set()
#         extra_where = []
#         extra_params = []
#         for key,item in self.get_linked_fields().items():
#             key_query_alias = KEY_QUERY_ALIAS_PATTERN.format(key=key)
#             
#             field_name = item.get('field_name', None)
#             if not field_name:
#                 field_name = item.get('linked_field_value_field',key)
#             
#             linkedModel = item.get('linked_field_model')
#             field_table = linkedModel._meta.db_table
#             
#             parent_table = query.model._meta.db_table
#             parent_table_key = query.model._meta.pk.name
# 
#             format_dict = {
#                 'field_name': field_name, 
#                 'field_table': field_table,
#                 'linked_field_parent': item['linked_field_parent'],
#                 'parent_table': parent_table,
#                 'parent_table_key': parent_table_key }
#             
#             if item['linked_field_type'] != 'fields.ListField':
#                 sql = ( 'select {field_name} from {field_table} {where}')
#                 where = ('WHERE {field_table}.{linked_field_parent}_id'
#                             '={parent_table}.{parent_table_key} ')
#                 
#                 if item.get('linked_field_meta_field', None):
#                     format_dict['meta_field'] = item['linked_field_meta_field']
#                     meta_field_id = getattr(item['meta_field_instance'], 'pk')
#                     where += ' and {field_table}.{meta_field}_id=%s '
#                     extra_params.append(meta_field_id)
#                 format_dict['where'] = where.format(**format_dict)
#                 sql = sql.format(**format_dict)
#                 extra_select[key_query_alias] = sql
#             if item['linked_field_type'] == 'fields.ListField':
#                 sql = \
# '''    (select $$["$$ || array_to_string(array_agg({field_name}), $$","$$) || $$"]$$
#         from (select {field_name} from {field_table} 
#         {where} {order_by}) a) '''
#                 
#                 where = ('WHERE {field_table}.{linked_field_parent}_id'
#                             '={parent_table}.{parent_table_key} ')
# 
#                 if item.get('linked_field_meta_field', None):
#                     format_dict['meta_field'] = item['linked_field_meta_field']
#                     meta_field_id = getattr(item['meta_field_instance'], 'pk')
#                     where += ' and {field_table}.{meta_field}_id=%s '
#                     extra_params.append(meta_field_id)
#                 else:
#                     pass
#                 
#                 format_dict['order_by'] = ''
#                 ordinal_field = item.get('ordinal_field', None)
#                 if ordinal_field:
#                     format_dict['order_by'] = ' ORDER BY %s ' % ordinal_field
#                 format_dict['where'] = where.format(**format_dict)
#                 sql = sql.format(**format_dict)
#                 extra_select[key_query_alias] = sql
# 
#         query = query.extra(
#             select=extra_select, where=extra_where,select_params=extra_params )
#         logger.debug(str(('==== query', query.query.sql_with_params())))
#         return query
#      
#     def dehydrate(self, bundle):
#         try:
#             keys_not_available = []
#             for key,item in self.get_linked_fields().items():
#                 key_query_alias = KEY_QUERY_ALIAS_PATTERN.format(key=key)
#                 bundle.data[key] = None
#                 if hasattr(bundle.obj, key_query_alias):
#                     bundle.data[key] = getattr(bundle.obj, key_query_alias)
#                     if bundle.data[key] and item['linked_field_type'] == 'fields.ListField':
#                         bundle.data[key] = json.loads(bundle.data[key])
#                 else:
#                     keys_not_available.append(key_query_alias)
#             if keys_not_available:
#                 logger.error(str(('keys not available', keys_not_available)))
#             return bundle
#         except Exception, e:
#             logger.exception('on dehydrate')
#             raise e
#         return bundle
#             
#     def dehydrate_inefficient(self, bundle):
#         '''
#         Note - dehydrate only to be used for small sets. 
#         Looks each of the the fields up as separare query; should be able to modify
#         the parent query and find these fields in it using ORM methods 
#         (TODO: we will implement obj-get-list methods)
#         '''
#         try:
#             for key,item in self.get_linked_fields().items():
#                 bundle.data[key] = None
#                 linkedModel = item.get('linked_field_model')
#                 queryparams = { item['linked_field_parent']: bundle.obj }
#                 if item.get('linked_field_meta_field', None):
#                     queryparams[item['linked_field_meta_field']] = item['meta_field_instance']
#                 if item['linked_field_type'] != 'fields.ListField':
#                     try:
#                         linkedObj = linkedModel.objects.get(**queryparams)
#                         bundle.data[key] = getattr( linkedObj, item['linked_field_value_field'])
#                     except ObjectDoesNotExist:
#                         pass
#                 else:
#                     query = linkedModel.objects.filter(**queryparams)
#                     if hasattr(linkedModel, 'ordinal'):
#                         query = query.order_by('ordinal')
#                     values = query.values_list(
#                             item['linked_field_value_field'], flat=True)
#                     if values and len(values)>0:
#                         bundle.data[key] = list(values)
#             return bundle
#         except Exception, e:
#             logger.exception('dehydrate')
#             raise
#         return bundle
#     
#     
# class RecordResource(ManagedLinkedResource):
#     ''' poc: store resource virtual fields in a related table
#     '''
#     class Meta:
#         queryset = Record.objects.all()
#         authentication = MultiAuthentication(
#             BasicAuthentication(), SessionAuthentication())
#         authorization= SuperUserAuthorization()        
# 
#         ordering = []
#         filtering = {'scope':ALL}
#         serializer = LimsSerializer()
#         excludes = [] #['json_field']
#         always_return_data = True # this makes Backbone happy
#         resource_name='record' 
# 
#     def __init__(self, **kwargs):
#         super(RecordResource,self).__init__(**kwargs)
            

