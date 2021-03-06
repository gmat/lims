from __future__ import unicode_literals

from collections import OrderedDict
import filecmp
import json
import logging
import os
import re
import sys

from django.contrib.auth.models import User
from django.core.urlresolvers import resolve
from django.db import connection
from django.test import TestCase
from django.test.client import MULTIPART_CONTENT
from django.utils.timezone import now
from tastypie.test import ResourceTestCase, TestApiClient
import xlrd

from db.models import Reagent, Substance, Library, ScreensaverUser, \
    UserChecklistItem, AttachedFile, ServiceActivity, Screen, Well, Publication, \
    PlateLocation, LibraryScreening
import db.models
from db.support import lims_utils, screen_result_importer
from db.test.factories import LibraryFactory, ScreenFactory, \
    ScreensaverUserFactory
from reports import ValidationError, HEADER_APILOG_COMMENT, _now
from reports.api import API_MSG_COMMENTS, API_MSG_CREATED, \
    API_MSG_SUBMIT_COUNT, API_MSG_UNCHANGED, API_MSG_UPDATED, \
    API_MSG_ACTION, API_MSG_RESULT
from db.api import API_MSG_SCREENING_PLATES_UPDATED, \
    API_MSG_SCREENING_ADDED_PLATE_COUNT, API_MSG_SCREENING_DELETED_PLATE_COUNT,\
    API_MSG_SCREENING_EXTANT_PLATE_COUNT,API_MSG_SCREENING_TOTAL_PLATE_COUNT
from reports.models import ApiLog, UserProfile, UserGroup, API_ACTION_PATCH,\
    API_ACTION_CREATE
from reports.serialize import XLSX_MIMETYPE, SDF_MIMETYPE, JSON_MIMETYPE
from reports.serializers import CSVSerializer, XLSSerializer, LimsSerializer, \
    ScreenResultSerializer
from reports.tests import IResourceTestCase, equivocal
from reports.tests import assert_obj1_to_obj2, find_all_obj_in_list, \
    find_obj_in_list, find_in_dict
from django.core.exceptions import ObjectDoesNotExist
from decimal import Decimal


logger = logging.getLogger(__name__)


BASE_URI = '/db/api/v1'
BASE_REPORTS_URI = '/reports/api/v1'
import db; 
try:
    APP_ROOT_DIR = os.path.abspath(os.path.dirname(db.__path__[0]))
except:
    APP_ROOT_DIR = os.path.abspath(os.path.dirname(db.__path__))
BASE_URI_DB = '/db/api/v1'


class DBResourceTestCase(IResourceTestCase):
    """
    Invoke _bootstrap_init_files on setup
    """
    def __init__(self,*args,**kwargs):
    
        super(DBResourceTestCase, self).__init__(*args,**kwargs)
        self.directory = os.path.join(APP_ROOT_DIR, 'db/static/api_init')

    def _bootstrap_init_files(self):
        
        super(DBResourceTestCase, self)._bootstrap_init_files()
        self.directory = os.path.join(APP_ROOT_DIR, 'db/static/api_init')
        serializer=CSVSerializer() 
        testApiClient = TestApiClient(serializer=serializer) 
        input_actions_file = os.path.join(
            self.directory, 'api_init_actions.csv')
        logger.info('open input_actions file: %r', input_actions_file)
        self._run_api_init_actions(input_actions_file)
    
    def create_library(self, data=None):
        ''' Create a test Library through the API'''

        input_data = LibraryFactory.attributes()
        if data:
            input_data.update(data)
        resource_uri = '/'.join([BASE_URI_DB, 'library'])
        test_uri = '/'.join([resource_uri,input_data['short_name']])
        return self._create_resource(input_data,resource_uri,test_uri)

    @staticmethod
    def create_small_molecule_test_well(
        plate, well_index, platesize=384, **kwargs):
        ''' Generate a test well for library initialization'''
        
        library_well_types = [
            'empty','experimental','dmso','library_control' ]
        well_name = lims_utils.well_name_from_index(well_index, platesize)
        library_well_type = kwargs.get(
            'library_well_type',library_well_types[well_index%3])
        _data = {
            'plate_number': plate, 
            'well_name': well_name,
            'well_id': '%s:%s' % (str(plate).zfill(5),well_name),
            'library_well_type' : library_well_type,
        }
        if library_well_type == 'experimental':
            _data['molar_concentration'] = '0.00%d' % (well_index+1)
            _data['vendor_name'] = 'vendorX'
            _data['vendor_identifier'] = 'ID-%d' % well_index
            _data['vendor_batch_id'] = 2
            _data['smiles'] = \
                'H%dC%dN%d' % (well_index%10,well_index%11,well_index%12)
            _data['compound_name'] = \
                ['name-%d'%well_index, 'name-%d'%((well_index+11)%100)]
    
        for k,v in kwargs.items():
            _data[k] = v
        return _data

    def create_screen(self, data=None):
        ''' Create a test Screen through the API'''
        
        lab_head = self.create_screensaveruser()
        lead_screener = self.create_screensaveruser()
        input_data = ScreenFactory.attributes()
        logger.info('input_data: %r', input_data)
        if data:
            input_data.update(data)
        if 'lab_head_username' not in input_data:
            input_data['lab_head_username'] = lab_head['username']
        if 'lead_screener_username' not in input_data:
            input_data['lead_screener_username'] = lead_screener['username']
            
        resource_uri = '/'.join([BASE_URI_DB, 'screen'])
        _data_for_get = { 
            'limit': 0,
            'includes': '*'
        }
        logger.info('screen input_data to create: %r', input_data)
        logger.info('post to %r...', resource_uri)
        resp = self.api_client.post(
            resource_uri, format='json', data=input_data, 
            authentication=self.get_credentials())
        new_obj = self.deserialize(resp)
        self.assertTrue(
            resp.status_code in [200,201], 
            (resp.status_code, self.get_content(resp)))
    
        new_obj = self.get_screen(new_obj['facility_id'])    
        result,msg = assert_obj1_to_obj2(input_data,new_obj)
        self.assertTrue(result, msg)
        logger.debug('item created: %r', new_obj)
        return new_obj

    def get_screen(self, facility_id, data_for_get=None):
        ''' Retrieve a Screen from the API'''
        
        resource_uri = '/'.join([BASE_URI_DB, 'screen', facility_id])
        return self.get_single_resource(resource_uri, data_for_get)

    def create_screensaveruser(self, data=None):
        ''' Create a test ScreensaverUser through the API'''
        
        input_data = ScreensaverUserFactory.attributes()
        if data:
            input_data.update(data)
        resource_uri = '/'.join([BASE_URI_DB, 'screensaveruser'])
        test_uri = '/'.join([resource_uri,input_data['username']])
        
        return self._create_resource(input_data,resource_uri,test_uri)
        
def setUpModule():

    logger.info('=== setup Module')
    keepdb = False
    reinit_metahash = False
    if len(sys.argv) > 1:
        for arg in sys.argv:
            print 'arg: ', arg
            if 'keepdb' in arg:
                keepdb = True
            if 'reinit_metahash' in arg:
                reinit_metahash = True
    # Set up a superuser
    try:
        logger.info('create/find superuser %s...', IResourceTestCase.username)
        IResourceTestCase.user = User.objects.get(username=IResourceTestCase.username)
        logger.info('superuser found: %r', IResourceTestCase.user)
        logger.info('users: %r', [str(u) for u in User.objects.all()])
    except ObjectDoesNotExist:
        logger.info('creating superuser: %s', IResourceTestCase.username)
        IResourceTestCase.user = User.objects.create_superuser(
            IResourceTestCase.username, '1testsuperuser@example.com', IResourceTestCase.password)
        logger.info('superuser created.')

    if reinit_metahash or not keepdb:
        testContext = DBResourceTestCase(methodName='_bootstrap_init_files')
        testContext.setUp()
        testContext._bootstrap_init_files()
    else:
        print 'skip database metahash initialization when using keepdb'

    try:
        su = ScreensaverUser.objects.get(username=IResourceTestCase.username)
        logger.info('found ss user: %r', su)
        temp_test_case = DBResourceTestCase(methodName='get_from_server')
        resource_uri = BASE_URI_DB + '/screensaveruser/' +IResourceTestCase.username
        DBResourceTestCase.admin_user = temp_test_case.get_from_server(resource_uri)
        logger.info('got admin user: %r', DBResourceTestCase.admin_user)
    except Exception:
        logger.info('create an admin screensaveruser...')
        temp_test_case = DBResourceTestCase(methodName='create_screensaveruser')
        DBResourceTestCase.admin_user = temp_test_case.create_screensaveruser({ 
            'username': temp_test_case.username,
            'is_superuser': True
        })
        logger.info('admin screensaveruser created')
    
    logger.info('=== setup Module done')

def tearDownModule():

    logger.info('=== teardown Module')
    # remove the admin user
    # ScreensaverUser.objects.all().delete() 
    # UserProfile.objects.all().delete()
    # User.objects.all().delete()

class LibraryResource(DBResourceTestCase):

    def setUp(self):

        super(LibraryResource, self).setUp()

    def tearDown(self):

        DBResourceTestCase.tearDown(self)
        logger.info('delete library resources')
        Library.objects.all().delete()
        # removing the library should remove dependent resources
        # Well.objects.all().delete()
        # Reagent.objects.all().delete()
        PlateLocation.objects.all().delete()
        ApiLog.objects.all().delete()
    
    def test1_create_library(self):

        logger.info('test1_create_library ...')
        
        resource_uri = BASE_URI_DB + '/library'
        
        library1 = self.create_library({ 
            'short_name': 'testlibrary1','start_plate': '1534', 
            'end_plate': '1534', 'plate_size': '384' })
        library2 = self.create_library({ 
            'short_name': 'testlibrary2','start_plate': '1535', 
            'end_plate': '1537', 'plate_size': '384' })
        
        logger.info('Find the library wells that were created...')
        resource_uri = '/'.join([
            BASE_URI_DB,'library',library2['short_name'],'well'])
        data_for_get={ 'limit': 0, 'includes': ['*', '-structure_image'] }
        resp = self.api_client.get(
            resource_uri, format='json', authentication=self.get_credentials(), 
            data=data_for_get)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code,self.get_content(resp)))
        self.assertTrue(resp['Content-Type'].startswith('application/json'))
        new_obj = self.deserialize(resp)
        expected_count = 384*3
        self.assertEqual(
            len(new_obj['objects']), expected_count, 
            'wrong number of wells: %d, expected: %d' 
                % (len(new_obj['objects']), expected_count))
        
        index = 0
        platesize = 384
        plate = 1535        
        substance_ids = set()
        # Examine wells - first plate only for speed
        for j in range(384):
            well_name = lims_utils.well_name_from_index(j, platesize)
            well_id = lims_utils.well_id(plate,well_name)
            well_search = {'well_id': well_id}
            result, well = find_obj_in_list(well_search, new_obj['objects'])
            self.assertTrue(result, well)
            
        logger.info('Load wells to the library...')
        plate = 1534
        input_data = [
            self.create_small_molecule_test_well(plate,i) for i in range(0,384)]
        resource_name = 'well'
        resource_uri = '/'.join([
            BASE_URI_DB,'library', library1['short_name'],resource_name])
        resp = self.api_client.put(
            resource_uri, format='sdf', data={ 'objects': input_data } , 
            authentication=self.get_credentials(), **data_for_get )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        
        # Examine Well/Reagents created:
        # NOTE: get the library "reagents" instead of the wells: reagents have 
        # one-to-many reln to wells, so the well endpoint returns only first;
        # also, the well resource is not the linked resource type, and does not
        # have the reagent fields.  
        resource_name = 'reagent'
        reagent_resource_uri = '/'.join([
            BASE_URI_DB,'library', library1['short_name'],resource_name])
        new_obj = self.get_from_server(resource_uri, data_for_get)
        returned_data = new_obj['objects']
        expected_count = 384
        self.assertEqual(
            len(returned_data), expected_count, 
            ('expected', expected_count, 'found',len(returned_data)))
        
        specific_schema = self.get_from_server(resource_uri + '/schema')
        fields = specific_schema['fields']
        self.validate_wells(input_data, returned_data, fields)
        
    def test1a_create_library_comments(self):

        logger.info('test1a_create_library_comments ...')
        
        resource_uri = BASE_URI_DB + '/library'
        
        library1 = self.create_library({ 
            'short_name': 'testlibrary1','start_plate': '1534', 
            'end_plate': '1534', 'plate_size': '384' })
        
        patch_uri = '/'.join([resource_uri,library1['short_name']]) 
        
        test_comment = 'Some test comment 123 xyz'
        
        header_data = { HEADER_APILOG_COMMENT: test_comment}
        
        resp = self.api_client.patch(
            patch_uri, format='json', #data={ 'objects': [ copywell_input] }, 
            authentication=self.get_credentials(), **header_data )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        patch_response = self.deserialize(resp)
        
        self.assertTrue('comments' in patch_response, 'patch_response: %r' % patch_response)
        self.assertTrue(test_comment in patch_response['comments'], 
            'test_comment: %r not found in library response obj: %r' % (
                test_comment, patch_response))

        test_comment2 = 'another test comment...'
        
        header_data = { HEADER_APILOG_COMMENT: test_comment2}
        
        resp = self.api_client.patch(
            patch_uri, format='json', #data={ 'objects': [ copywell_input] }, 
            authentication=self.get_credentials(), **header_data )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        patch_response = self.deserialize(resp)
        
        self.assertTrue('comments' in patch_response)
        self.assertTrue(test_comment in patch_response['comments'], 
            'test_comment: %r not found in library response obj: %r' % (
                test_comment, patch_response))
        self.assertTrue(test_comment2 in patch_response['comments'], 
            'test_comment2: %r not found in library response obj: %r' % (
                test_comment2, patch_response))
        
    def validate_wells(self, input_data, output_data, fields):
        ''' Validate that the input well/reagents were created in the output_data'''
        substance_ids = set()
        for inputobj in input_data:
            # 1. Validate well/reagents exist
            search = { 'well_id': 
                lims_utils.well_id(
                    inputobj['plate_number'],inputobj['well_name']) }
            result, outputobj = find_obj_in_list(
                search,output_data) #, excludes=excludes )
            self.assertTrue(
                result, 
                ('not found', search,outputobj,'=== objects returned ===', 
                      output_data ) ) 
            # 2. Validate well/reagent fields
            expected_data = { 
                key: inputobj[key] for key in fields.keys() if key in inputobj }
            result, msgs = assert_obj1_to_obj2(expected_data, outputobj)
            self.assertTrue(
                result, (msgs, 'input', expected_data, 'output', outputobj))

            substance_id = outputobj['substance_id']
            self.assertTrue(
                substance_id not in substance_ids, 
                ('substance_id not unique', substance_id, substance_ids))
            substance_ids.add(substance_id)
        
    def test10_create_library_copy_specific_wells(self):

        logger.info('test10_create_library_copy ...')
        
        # 1. Create Library
        logger.info('create library a library...')
        start_plate = 1000
        end_plate = 1005
        plate_size = 384
        library_data = self.create_library({
            'start_plate': start_plate, 
            'end_plate': end_plate,
            'plate_size': plate_size,
            'screen_type': 'small_molecule' })
        short_name = library_data['short_name']
        
        # 2. Create Library Wells: wells have various concentrations 
        logger.info('create and load well data, plates: %r-%r...', 
            start_plate, end_plate)
        well_input_data = {}
        for plate in range(start_plate, end_plate+1):
            for i in range(0,plate_size):
                # Create test wells, concentrations will be varied
                well_input = self.create_small_molecule_test_well(
                    plate,i,library_well_type='experimental')
                well_input_data[well_input['well_id']] = well_input
        resource_name = 'well'
        resource_uri = '/'.join([
            BASE_URI_DB,'library', short_name, resource_name])
        resp = self.api_client.put(
            resource_uri, format='sdf', data={ 'objects': well_input_data.values() } , 
            authentication=self.get_credentials(), 
            **{ 'limit': 0, 'includes': '*'} )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        
        # 3. Create a Library Copy:
        # - will also create Plates: 
        # -- initial_well_volume will be set
        # -- because the well concentrations vary, Plate.concentration fields are not set
        logger.info('Create library copy...')
        copy_input_data = {
            'library_short_name': library_data['short_name'],
            'copy_name': "A",
            'usage_type': "library_screening_plates",
            'initial_plate_well_volume': '0.000040'
        
        }        
        resource_uri = BASE_URI_DB + '/librarycopy'
        resource_test_uri = '/'.join([
            resource_uri,copy_input_data['library_short_name'],copy_input_data['copy_name']])
        library_copy = self._create_resource(
            copy_input_data, resource_uri, resource_test_uri, 
            excludes=['initial_plate_well_volume'])
        
        # 4. Verify created Plates
        logger.info('Verify plates created...')
        uri = '/'.join([resource_test_uri,'plate'])
        data_for_get={ 'limit': 0 }        
        data_for_get.setdefault('includes', ['*'])
        resp = self.api_client.get(
            uri, format='json', authentication=self.get_credentials(), 
            data=data_for_get)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code,self.get_content(resp)))
        new_obj = self.deserialize(resp)
        start_plate = int(library_data['start_plate'])
        end_plate = int(library_data['end_plate'])
        number_of_plates = end_plate-start_plate+1
        self.assertEqual(len(new_obj['objects']),number_of_plates)
        for plate_data in new_obj['objects']:
            self.assertEqual(library_copy['copy_name'],plate_data['copy_name'])
            plate_number = int(plate_data['plate_number'])
            self.assertTrue(
                plate_number>=start_plate and plate_number<=end_plate,
                'wrong plate_number: %r' % plate_data)
            self.assertEqual(
                Decimal(copy_input_data['initial_plate_well_volume']),
                Decimal(plate_data['well_volume']))
            self.assertTrue(plate_data['molar_concentration'] is None)
            self.assertTrue(plate_data['mg_ml_concentration'] is None)
            # Min Molar concentration is set, because copy_wells were created
            self.assertTrue(plate_data['min_molar_concentration'] is not None)
            # Min mg/ml concentration is not set, because copy_wells only have molar
            self.assertTrue(plate_data['min_mg_ml_concentration'] is None)
            self.assertEqual(plate_data['status'], 'not_specified')
            
        # 5. Verify created CopyWells
        logger.info('Verify copy_wells created (check varying concentrations)...')
        uri = '/'.join([
            BASE_URI_DB,
            'library',
            copy_input_data['library_short_name'],
            'copy',
            copy_input_data['copy_name'],
            'copywell'])
        data_for_get={ 'limit': 0 }        
        data_for_get.setdefault('includes', ['*'])
        resp = self.api_client.get(
            uri, format='json', authentication=self.get_credentials(), 
            data=data_for_get)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code,self.get_content(resp)))
        new_obj = self.deserialize(resp)
        copy_wells_returned = new_obj['objects']
        self.assertEqual(len(copy_wells_returned),len(well_input_data))

        for copy_well_data in copy_wells_returned:
            self.assertEqual(library_copy['copy_name'],copy_well_data['copy_name'])
            self.assertTrue(
                copy_well_data['plate_number'] >= start_plate
                and copy_well_data['plate_number'] <= end_plate,
                'copy_well returned for wrong plate: %r' % copy_well_data )
            well_input = well_input_data.get(copy_well_data['well_id'], None)
            self.assertTrue(well_input is not None, 
                'copy well not in wells: %r' % copy_well_data)
            if well_input['library_well_type'] == 'experimental':
                self.assertEqual(
                    Decimal(copy_input_data['initial_plate_well_volume']),
                    Decimal(copy_well_data['initial_volume']))
                self.assertEqual(
                    copy_well_data['initial_volume'], 
                    copy_well_data['volume'])
                self.assertEqual(
                    Decimal(well_input['molar_concentration']),
                    Decimal(copy_well_data['molar_concentration']), 
                    'molar concentration mismatch: %r output %r' 
                    % (well_input,copy_well_data))
                self.assertTrue(copy_well_data['mg_ml_concentration'] is None)
            else:
                self.assertTrue(
                    copy_well_data['initial_volume'] is None,
                    'non-experimental well has data: %r' % copy_well_data)
                self.assertTrue(copy_well_data['volume'] is None,
                    'non-experimental well has data: %r' % copy_well_data)
                self.assertTrue(copy_well_data['molar_concentration'] is None,
                    'non-experimental well has data: %r' % copy_well_data)
        return (library_data, library_copy, copy_wells_returned)

    def test10a_create_library_copy_simple_wells(self):

        logger.info('test10a_create_library_copy_simple_wells ...')
        logger.info('creates a library wells with single concentration')
        
        # 1. Create a Library
        logger.info('create library a library...')
        start_plate = 1000
        end_plate = 1005
        plate_size = 384
        library_data = self.create_library({
            'start_plate': start_plate, 
            'end_plate': end_plate,
            'plate_size': plate_size,
            'screen_type': 'small_molecule' })
        short_name = library_data['short_name']
        
        # 2. Create Library Wells: wells all have the same concentration 
        logger.info('create and load well data, plates: %r-%r...', 
            start_plate, end_plate)
        well_input_data = {}
        single_molar_concentration = '0.010'
        for plate in range(start_plate, end_plate+1):
            for i in range(0,plate_size):
                well_input = self.create_small_molecule_test_well(
                    plate,i)
                if well_input['library_well_type'] == 'experimental':
                    well_input['molar_concentration'] = single_molar_concentration
                well_input_data[well_input['well_id']] = well_input
        resource_name = 'well'
        resource_uri = '/'.join([
            BASE_URI_DB,'library', short_name, resource_name])
        resp = self.api_client.put(
            resource_uri, format='sdf', data={ 'objects': well_input_data.values() } , 
            authentication=self.get_credentials(), 
            **{ 'limit': 0, 'includes': '*'} )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))

        # 3. Create a Library Copy        
        logger.info('create library copy...')
        copy_input_data = {
            'library_short_name': library_data['short_name'],
            'copy_name': "A",
            'usage_type': "library_screening_plates",
            'initial_plate_well_volume': '0.000040'
        }        
        resource_uri = BASE_URI_DB + '/librarycopy'
        resource_test_uri = '/'.join([
            resource_uri,copy_input_data['library_short_name'],copy_input_data['copy_name']])
        library_copy = self._create_resource(
            copy_input_data, resource_uri, resource_test_uri, 
            excludes=['initial_plate_well_volume'])
        
        # 4. Verify created Plates
        logger.info('Verify plates created...')
        uri = '/'.join([resource_test_uri,'plate'])
        data_for_get={ 'limit': 0 }        
        data_for_get.setdefault('includes', ['*'])
        resp = self.api_client.get(
            uri, format='json', authentication=self.get_credentials(), 
            data=data_for_get)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code,self.get_content(resp)))
        new_obj = self.deserialize(resp)
        start_plate = int(library_data['start_plate'])
        end_plate = int(library_data['end_plate'])
        number_of_plates = end_plate-start_plate+1
        self.assertEqual(len(new_obj['objects']),number_of_plates)
        for plate_data in new_obj['objects']:
            self.assertEqual(library_copy['copy_name'],plate_data['copy_name'])
            plate_number = int(plate_data['plate_number'])
            self.assertTrue(
                plate_number>=start_plate and plate_number<=end_plate,
                'wrong plate_number: %r' % plate_data)
            self.assertEqual(
                Decimal(copy_input_data['initial_plate_well_volume']),
                Decimal(plate_data['well_volume']))
            self.assertEqual(
                Decimal(plate_data['molar_concentration']),
                Decimal(single_molar_concentration))
            self.assertEqual(
                plate_data['molar_concentration'],
                plate_data['min_molar_concentration'])
            self.assertTrue(plate_data['mg_ml_concentration'] is None)
            self.assertTrue(plate_data['min_mg_ml_concentration'] is None)

        # 5. Verify CopyWell data can be queried, but that all have single concentration
        logger.info('Verify copy_wells created ()...')
        uri = '/'.join([
            BASE_URI_DB,
            'library',
            copy_input_data['library_short_name'],
            'copy',
            copy_input_data['copy_name'],
            'copywell'])
        data_for_get={ 'limit': 0 }        
        data_for_get.setdefault('includes', ['*'])
        resp = self.api_client.get(
            uri, format='json', authentication=self.get_credentials(), 
            data=data_for_get)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code,self.get_content(resp)))
        new_obj = self.deserialize(resp)
        copy_wells_returned = new_obj['objects']
        self.assertEqual(len(copy_wells_returned),len(well_input_data))

        for copy_well_data in copy_wells_returned:
            self.assertEqual(library_copy['copy_name'],copy_well_data['copy_name'])
            self.assertTrue(
                copy_well_data['plate_number'] >= start_plate
                and copy_well_data['plate_number'] <= end_plate,
                'copy_well returned for wrong plate: %r' % copy_well_data )
            well_input = well_input_data.get(copy_well_data['well_id'], None)
            self.assertTrue(well_input is not None, 
                'copy well not in wells: %r' % copy_well_data)
            if well_input['library_well_type'] == 'experimental':
                self.assertEqual(
                    Decimal(copy_input_data['initial_plate_well_volume']),
                    Decimal(copy_well_data['initial_volume']))
                self.assertEqual(
                    Decimal(copy_well_data['initial_volume']), 
                    Decimal(copy_well_data['volume']))
                self.assertEqual(
                    Decimal(well_input['molar_concentration']),
                    Decimal(copy_well_data['molar_concentration']), 
                    'molar concentration mismatch: %r output %r' %(well_input,copy_well_data))
                self.assertTrue(copy_well_data['mg_ml_concentration'] is None)
            else:
                self.assertTrue(
                    copy_well_data['initial_volume'] is None,
                    'non-experimental well has data: %r' % copy_well_data)
                self.assertTrue(copy_well_data['volume'] is None,
                    'non-experimental well has data: %r' % copy_well_data)
                self.assertTrue(copy_well_data['molar_concentration'] is None,
                    'non-experimental well has data: %r' % copy_well_data)
        return (library_data, library_copy, copy_wells_returned)
        
    def test11_create_library_copy_invalids(self):
        # TODO: try to create duplicate copy names
        # TODO: try to create invalid types, plate ranges
        
        pass
    
    def test12_modify_copy_wells(self):

        logger.info('test12_modify_copy_wells ...')
    
        (library_data, copy_data, plate_data) = self.test10_create_library_copy_specific_wells()
        end_plate = int(library_data['end_plate'])
        start_plate = int(library_data['start_plate'])
        short_name = library_data['short_name']
        plate_size = int(library_data['plate_size'])

        logger.info('retrieve the copy_wells...')
        resource_uri = '/'.join([
            BASE_URI_DB,'library',library_data['short_name'],'copy',
            copy_data['copy_name'],'copywell'])
        data_for_get={ 'limit': 0 }        
        data_for_get.setdefault('includes', ['*'])
        data_for_get['plate_number__eq'] = start_plate
        resp = self.api_client.get(
            resource_uri, format='json', authentication=self.get_credentials(), 
            data=data_for_get)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code,self.get_content(resp)))
        new_obj = self.deserialize(resp)
        
        copywell_data = new_obj['objects']
        expected_wells = plate_size;
        logger.info('returned copywell: %r', copywell_data[0])
        logger.info('returned copywell: %r', copywell_data[-1])
        self.assertEqual(len(copywell_data),expected_wells)
        
        logger.info('patch a copywell...')
        
        copywell_input = copywell_data[0]
        original_volume = float(copywell_input['volume'])
        volume_adjustment = 0.000008
        copywell_input['volume'] = round(original_volume-volume_adjustment, 8)
        
        patch_uri = '/'.join([resource_uri,copywell_input['well_id']]) 
        resp = self.api_client.patch(
            patch_uri, format='json', data=copywell_input, 
            authentication=self.get_credentials(), **data_for_get )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        patch_response = self.deserialize(resp)
        new_copywell = patch_response
        self.assertEqual(
            volume_adjustment, float(new_copywell['consumed_volume']))
        self.assertEqual(
            float(copywell_input['volume']), float(new_copywell['volume']))
    
    def test13_plate_locations(self):

        logger.info('test13_plate_locations ...')
        (library_data, copy_data, plate_data) = self.test10_create_library_copy_specific_wells()
        
        end_plate = library_data['end_plate']
        start_plate = library_data['start_plate']
        short_name = library_data['short_name']
        
        # 1. Simple test
        lps_format = '{library_short_name}:{copy_name}:{start_plate}-{end_plate}'
        copy_plate_ranges = [
            lps_format.format(
                library_short_name=library_data['short_name'],
                copy_name=copy_data['copy_name'],
                start_plate=start_plate,
                end_plate=end_plate,)
        ]
        
        plate_location_input = {
            'room': 'room1', 'freezer': 'freezer1', 'shelf': 'shelf1',
            'bin': 'bin1',
            'copy_plate_ranges': copy_plate_ranges,
        }
        
        resource_uri = BASE_URI_DB + '/platelocation'
        resource_test_uri = resource_uri
        plate_location = self._create_resource(
            plate_location_input, resource_uri, resource_test_uri,)
        self.assertEqual(
            plate_location['copy_plate_ranges'],
            plate_location_input['copy_plate_ranges'],
            'plate ranges not equal: %r - %r' % (
                plate_location['copy_plate_ranges'],
                plate_location_input['copy_plate_ranges']))

        # Verify that plates have the location set as well
        resource_uri = BASE_URI_DB + '/librarycopyplate'
        resp = self.api_client.get(
            resource_uri,format='json', 
            data={ 
                'plate_number__range': [start_plate,end_plate],
                'copy_name__eq': copy_data['copy_name']
                }, 
            authentication=self.get_credentials(),)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        plates_data = new_obj['objects']
        self.assertTrue(len(plates_data)==end_plate-start_plate+1, 
            'could not find all of the plates in the range: %r, %r'
            % ([start_plate,end_plate],plates_data))
        location_fields = ['room','freezer','shelf','bin']
        for plate_data in plates_data:
            for field in location_fields:
                self.assertTrue(
                    plate_data[field]==plate_location_input[field],
                    'plate location: expected: %r, rcvd: %r'
                    % (plate_location_input, plate_data))
        
        # Test apilogs
        resource_uri = BASE_REPORTS_URI + '/apilog'
        
        # Test platelocation apilog
        resp = self.api_client.get(
            resource_uri, format='json', 
            authentication=self.get_credentials(), 
            data={ 
                'limit': 0, 
                'ref_resource_name': 'platelocation'})
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        expected_count = 1 # create
        self.assertEqual( 
            len(new_obj['objects']), expected_count , 
            str((len(new_obj['objects']), expected_count, new_obj)))
        
        # Test librarycopyplate apilogs
        resp = self.api_client.get(
            resource_uri, format='json', 
            authentication=self.get_credentials(), 
            data={ 
                'limit': 0, 
                'ref_resource_name': 'librarycopyplate'})
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        expected_count = 6 # one for each plate in the copy_range
        self.assertEqual( 
            len(new_obj['objects']), expected_count , 
            str((len(new_obj['objects']), expected_count, new_obj)))
        for logvalue in new_obj['objects']:
            diffs = json.loads(logvalue['diffs'])
            self.assertTrue(diffs['bin']==[None, 'bin1'],
                'wrong diff: %r' % diffs )

        # 2. remove plates from the range
        copy_plate_ranges = [
            lps_format.format(
                library_short_name=library_data['short_name'],
                copy_name=copy_data['copy_name'],
                start_plate=start_plate,
                end_plate=end_plate-2,)
        ]
        plate_location_input = {
            'room': 'room1', 'freezer': 'freezer1', 'shelf': 'shelf1',
            'bin': 'bin1',
            'copy_plate_ranges': copy_plate_ranges,
        }
        resource_uri = BASE_URI_DB + '/platelocation'
        
        # TODO: test multipart/form upload, which is what the client will use
        resp = self.api_client.patch(
            resource_uri,format='json', 
            data={'objects': [plate_location_input],}, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        # TODO: check for PATCH meta information
        
        resp = self.api_client.get(
            resource_uri,format='json', 
            data={ 'includes': ['copy_plate_ranges'],}, 
            authentication=self.get_credentials(),)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        logger.info('resp: %r', new_obj)
        new_obj = new_obj['objects'][0]
        self.assertEqual(
            new_obj['copy_plate_ranges'],
            plate_location_input['copy_plate_ranges'],
            'plate ranges not equal: %r - %r' % (
                new_obj['copy_plate_ranges'],
                plate_location_input['copy_plate_ranges']))
        
        # Verify that removed plates have a location set to null
        resource_uri = BASE_URI_DB + '/librarycopyplate'
        data_for_get={ 
            'plate_number__range': [end_plate-1,end_plate],
            'copy_name__eq': copy_data['copy_name']
            } 
        resp = self.api_client.get(
            resource_uri,format='json', data=data_for_get,
            authentication=self.get_credentials(),)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        self.assertTrue(len(new_obj['objects'])==2, 
            'could not find all modified plates for %r, %r'
            %(data_for_get, new_obj))
        for plate_data in new_obj['objects']:
            for field in location_fields:
                self.assertTrue(
                    plate_data[field]==None,
                    'plate location: %r should be None, %r'
                    % (field, plate_data))
    
    def test17_batch_edit_copyplate_info(self):
        
        logger.info('test17_batch_edit_copyplate_info ...')
        
        (library_data, copy_data, plate_data) = self.test10_create_library_copy_specific_wells()
        end_plate = library_data['end_plate']
        start_plate = library_data['start_plate']
        short_name = library_data['short_name']
        
        # 1. patch new data to the copyplates
        
        new_plate_data = {
            'status': 'available',
            'remaining_well_volume': '0.000030', 
            'plate_type': 'nunc_96'
        }
        resource_uri = BASE_URI_DB + '/librarycopyplate/batch_edit'
        
        data = {
            'data': { 'plate_info': new_plate_data }, 
            'search_data': {
                'library_short_name': library_data['short_name'],
                'copy_name': copy_data['copy_name'] }
            }
        
        resp = self.api_client.patch(
            resource_uri,format='json', 
            data=data, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        patch_response = self.deserialize(resp)
        
        # Inspect meta "Result" section
        self.assertTrue('meta' in patch_response, '%r' % patch_response)
        self.assertTrue(API_MSG_RESULT in patch_response['meta'], '%r' % patch_response)
        self.assertTrue(API_MSG_SUBMIT_COUNT in patch_response['meta'][API_MSG_RESULT], '%r' % patch_response)
        self.assertTrue(patch_response['meta'][API_MSG_RESULT][API_MSG_SUBMIT_COUNT]==6, 
            'Wrong "%r" count: %r' 
            % (API_MSG_SUBMIT_COUNT, patch_response['meta']))
        logger.info('patch_response: %r', patch_response)
        
        # 2. Verify plates
        resource_uri = BASE_URI_DB + '/librarycopyplate'
        resp = self.api_client.get(
            resource_uri,format='json', 
            data={ 
                'library_short_name__eq': library_data['short_name'],
                'copy_name__eq': copy_data['copy_name']
                }, 
            authentication=self.get_credentials(),)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        
        plates_data = new_obj['objects']
        self.assertTrue(len(plates_data)==end_plate-start_plate+1, 
            'could not find all of the plates in the range: %r, %r'
            % ([start_plate,end_plate],plates_data))
        
        plates_data_output = plates_data
        for plate_data_output in plates_data_output:
            for field in new_plate_data.keys():
                self.assertTrue(
                    equivocal(plate_data_output[field],new_plate_data[field]),
                    'plate data: expected: %r, rcvd: %r'
                    % (new_plate_data[field],plate_data_output[field]))
            self.assertEqual(
                _now().date().strftime("%Y-%m-%d"), plate_data_output['date_plated'],
                'expected date_plated: %r, %r' %(_now().date(), plate_data_output['date_plated']))

        # 3. patch new data - status to retired
        new_plate_data['status'] = 'retired'
        resource_uri = BASE_URI_DB + '/librarycopyplate/batch_edit'
        
        data = {
            'data': { 'plate_info': new_plate_data }, 
            'search_data': {
                'library_short_name': library_data['short_name'],
                'copy_name': copy_data['copy_name'] }
            }
        
        resp = self.api_client.patch(
            resource_uri,format='json', 
            data=data, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        patch_response = self.deserialize(resp)
        
        # Inspect meta "Result" section
        self.assertTrue('meta' in patch_response, '%r' % patch_response)
        self.assertTrue(API_MSG_RESULT in patch_response['meta'], '%r' % patch_response)
        self.assertTrue(API_MSG_SUBMIT_COUNT in patch_response['meta'][API_MSG_RESULT], '%r' % patch_response)
        self.assertTrue(patch_response['meta'][API_MSG_RESULT][API_MSG_SUBMIT_COUNT]==6, 
            'Wrong "%r" count: %r' 
            % (API_MSG_SUBMIT_COUNT, patch_response['meta']))
        logger.info('patch_response: %r', patch_response)
        
        # 4. Verify plates status changed to "retired"
        resource_uri = BASE_URI_DB + '/librarycopyplate'
        resp = self.api_client.get(
            resource_uri,format='json', 
            data={ 
                'library_short_name__eq': library_data['short_name'],
                'copy_name__eq': copy_data['copy_name']
                }, 
            authentication=self.get_credentials(),)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        
        plates_data = new_obj['objects']
        self.assertTrue(len(plates_data)==end_plate-start_plate+1, 
            'could not find all of the plates in the range: %r, %r'
            % ([start_plate,end_plate],plates_data))
        
        plates_data_output = plates_data
        for plate_data_output in plates_data_output:
            self.assertEqual('retired', plate_data_output['status'])
            self.assertEqual(
                _now().date().strftime("%Y-%m-%d"), plate_data_output['date_retired'],
                'expected date_plated: %r, %r' %(_now().date(), plate_data_output['date_plated']))


    
    def test16_batch_edit_copyplate_location(self):
        
        logger.info('test16_batch_edit_copyplate_location ...')
        (library_data, copy_data, plate_data) = self.test10_create_library_copy_specific_wells()
        end_plate = library_data['end_plate']
        start_plate = library_data['start_plate']
        short_name = library_data['short_name']
        
        plate_location_input = {
            'room': 'room1', 'freezer': 'freezer1', 'shelf': 'shelf1',
            'bin': 'bin1',
        }
        
        resource_uri = BASE_URI_DB + '/librarycopyplate/batch_edit'
        
        data = {
            'data': { 'plate_location': plate_location_input }, 
            'search_data': {
                'library_short_name': library_data['short_name'],
                'copy_name': copy_data['copy_name'] }
            }
        
        logger.info('Patch batch_edit: cred: %r', self.username)
        resp = self.api_client.patch(
            resource_uri,format='json', 
            data=data, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        patch_response = self.deserialize(resp)
        
        # Inspect meta "Result" section
        self.assertTrue('meta' in patch_response, '%r' % patch_response)
        self.assertTrue(API_MSG_RESULT in patch_response['meta'], '%r' % patch_response)
        self.assertTrue(API_MSG_SUBMIT_COUNT in patch_response['meta'][API_MSG_RESULT], '%r' % patch_response)
        self.assertTrue(patch_response['meta'][API_MSG_RESULT][API_MSG_SUBMIT_COUNT]==6, 
            'Wrong %r count: %r' % (API_MSG_SUBMIT_COUNT,patch_response['meta']))
        
        # Get plates as defined
        resource_uri = BASE_URI_DB + '/librarycopyplate'
        resp = self.api_client.get(
            resource_uri,format='json', 
            data={ 
                'library_short_name__eq': library_data['short_name'],
                'copy_name__eq': copy_data['copy_name']
                }, 
            authentication=self.get_credentials(),)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        
        plates_data = new_obj['objects']
        self.assertTrue(len(plates_data)==end_plate-start_plate+1, 
            'could not find all of the plates in the range: %r, %r'
            % ([start_plate,end_plate],plates_data))
        
        plates_data_input = plates_data
        for plate_data in plates_data_input:
            for field in plate_location_input.keys():
                self.assertTrue(
                    plate_data[field]==plate_location_input[field],
                    'plate location: expected: %r, rcvd: %r'
                    % (plate_location_input[field],plate_data))
#                 plate_data[field] = plate_location_input[field]

        # check ApiLogs
        # one for each plate, one parent_log, one for each location
        resource_uri = BASE_REPORTS_URI + '/apilog'
        
        # Test platelocation apilog
        resp = self.api_client.get(
            resource_uri, format='json', 
            authentication=self.get_credentials(), 
            data={ 
                'limit': 0, 
                'ref_resource_name': 'platelocation'})
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        expected_count = 1 # created
        self.assertEqual( 
            len(new_obj['objects']), expected_count , 
            str((len(new_obj['objects']), expected_count, new_obj)))
        
        # Test librarycopyplate apilogs
        resp = self.api_client.get(
            resource_uri, format='json', 
            authentication=self.get_credentials(), 
            data={ 
                'limit': 0, 
                'ref_resource_name': 'librarycopyplate',
                'includes': ['added_keys']})
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        expected_count = 7 # one for each plate in the copy_range, one parent log
        self.assertEqual( 
            len(new_obj['objects']), expected_count , 
            str((len(new_obj['objects']), expected_count, new_obj)))
        for logvalue in new_obj['objects']:
            if logvalue['parent_log_uri']:
                diffs = json.loads(logvalue['diffs'])
                self.assertTrue(diffs['bin']==[None, 'bin1'],
                    'wrong diff: %r' % diffs )
            else:
                # parent log
                logger.info('parent log: %r', logvalue)
                self.assertTrue(logvalue['key']=='librarycopyplate',
                    'parent_log key should be "librarycopyplate", %r' % logvalue)
    
    def test15_modify_copyplate_info(self):

        logger.info('test15_modify_copyplate_info ...')
        
        (library_data, copy_data, plate_data) = self.test10_create_library_copy_specific_wells()
        end_plate = library_data['end_plate']
        start_plate = library_data['start_plate']
        short_name = library_data['short_name']
        
        new_plate_data = {
            'status': 'available',
            'remaining_well_volume': '0.000030', 
            'plate_type': 'nunc_96'
        }
        # 1. Get plates as defined
        resource_uri = BASE_URI_DB + '/librarycopyplate'
        resp = self.api_client.get(
            resource_uri,format='json', 
            data={ 
                'plate_number__range': [start_plate,end_plate],
                'copy_name__eq': copy_data['copy_name']
                }, 
            authentication=self.get_credentials(),)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        
        plates_data = new_obj['objects']
        self.assertTrue(len(plates_data)==end_plate-start_plate+1, 
            'could not find all of the plates in the range: %r, %r'
            % ([start_plate,end_plate],plates_data))
        
        plates_data_input = plates_data
        for plate_data in plates_data_input:
            plate_data.update(new_plate_data)

        # 2. Patch the plates
        logger.info('Patch the plates: cred: %r', self.username)
        resp = self.api_client.patch(
            resource_uri,format='json', 
            data={'objects': plates_data_input,}, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        patch_response = self.deserialize(resp)
        self.assertTrue('meta' in patch_response, '%r' % patch_response)
        self.assertTrue(API_MSG_RESULT in patch_response['meta'], '%r' % patch_response)
        self.assertTrue(API_MSG_SUBMIT_COUNT in patch_response['meta'][API_MSG_RESULT], '%r' % patch_response)
        self.assertTrue(
            patch_response['meta'][API_MSG_RESULT][API_MSG_SUBMIT_COUNT] == (end_plate-start_plate+1),
            '"%r" : %r, expected: %r' 
            % (API_MSG_SUBMIT_COUNT,
                patch_response['meta'], (end_plate-start_plate+1)))
        
        # 3. Verify that the plates have the expected location
        resp = self.api_client.get(
            resource_uri,format='json', 
            data={ 
                'plate_number__range': [start_plate,end_plate],
                'copy_name__eq': copy_data['copy_name']
                }, 
            authentication=self.get_credentials(),)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        plates_data_output = new_obj['objects']
        self.assertTrue(len(plates_data)==end_plate-start_plate+1, 
            'could not find all of the plates in the range: %r, %r'
            % ([start_plate,end_plate],plates_data))
        for plate_data in plates_data_output:
            for field in new_plate_data.keys():
                self.assertTrue(
                    equivocal(plate_data[field],new_plate_data[field]),
                    'plate data: expected: %r, rcvd: %r'
                    % (new_plate_data[field],plate_data[field]))
                self.assertEqual(
                    _now().date().strftime("%Y-%m-%d"), plate_data['date_plated'],
                    'expected date_plated: %r, %r' %(_now().date(), plate_data['date_plated']))

        # Test ApiLogs:
        # plate - one for each plate
        # plate_location - one for each plate addition to the range
        
    def test14_modify_copy_plate_locations(self):

        logger.info('test14_modify_copy_plate_locations ...')
        
        (library_data, copy_data, plate_data) = self.test10_create_library_copy_specific_wells()
        end_plate = library_data['end_plate']
        start_plate = library_data['start_plate']
        short_name = library_data['short_name']

        plate_location_input = {
            'room': 'Room1', 'freezer': 'Freezer1', 'shelf': 'Shelf1', 
            'bin': 'Bin1' }

        # Get plates as defined
        resource_uri = BASE_URI_DB + '/librarycopyplate'
        resp = self.api_client.get(
            resource_uri,format='json', 
            data={ 
                'plate_number__range': [start_plate,end_plate],
                'copy_name__eq': copy_data['copy_name']
                }, 
            authentication=self.get_credentials(),)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        
        plates_data = new_obj['objects']
        self.assertTrue(len(plates_data)==end_plate-start_plate+1, 
            'could not find all of the plates in the range: %r, %r'
            % ([start_plate,end_plate],plates_data))
        
        plates_data_input = plates_data
        for plate_data in plates_data_input:
            for field in plate_location_input.keys():
                self.assertTrue(
                    plate_data[field]==None,
                    'plate location: expected: None, rcvd: %r'
                    % (plate_data))
                plate_data[field] = plate_location_input[field]

        # Patch the plates
        logger.info('Patch the plates: cred: %r', self.username)
        resp = self.api_client.patch(
            resource_uri,format='json', 
            data={'objects': plates_data_input,}, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        patch_response = self.deserialize(resp)
        self.assertTrue('meta' in patch_response, '%r' % patch_response)
        self.assertTrue(API_MSG_RESULT in patch_response['meta'], '%r' % patch_response)
        self.assertTrue(API_MSG_SUBMIT_COUNT in patch_response['meta'][API_MSG_RESULT], '%r' % patch_response)
        self.assertTrue(
            patch_response['meta'][API_MSG_RESULT][API_MSG_SUBMIT_COUNT] == (end_plate-start_plate+1),
            '"%r" : %r, expected: %r' 
            % (API_MSG_SUBMIT_COUNT,
                patch_response['meta'], (end_plate-start_plate+1)))
        
        # Verify that the plates have the expected location
        resp = self.api_client.get(
            resource_uri,format='json', 
            data={ 
                'plate_number__range': [start_plate,end_plate],
                'copy_name__eq': copy_data['copy_name']
                }, 
            authentication=self.get_credentials(),)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        plates_data = new_obj['objects']
        self.assertTrue(len(plates_data)==end_plate-start_plate+1, 
            'could not find all of the plates in the range: %r, %r'
            % ([start_plate,end_plate],plates_data))
        for plate_data in plates_data:
            for field in plate_location_input.keys():
                self.assertTrue(
                    plate_data[field]==plate_location_input[field],
                    'plate location: expected: None, rcvd: %r'
                    % (plate_location_input))

        # Verify that the plate range is set on the location:
        lps_format = '{library_short_name}:{copy_name}:{start_plate}-{end_plate}'
        expected_copy_plate_ranges = [
            lps_format.format(
                library_short_name=library_data['short_name'],
                copy_name=copy_data['copy_name'],
                start_plate=start_plate,
                end_plate=end_plate)]
        
        plate_location_input['copy_plate_ranges'] = expected_copy_plate_ranges
        resource_uri = BASE_URI_DB + '/platelocation'
        resp = self.api_client.get(
            resource_uri,format='json', 
            data={ 'includes': ['copy_plate_ranges'],}, 
            authentication=self.get_credentials(),)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        logger.info('resp: %r', new_obj)
        self.assertTrue(
            len(new_obj['objects'])==1, 'too many locations were created')
        new_obj = new_obj['objects'][0]
        self.assertEqual(
            new_obj['copy_plate_ranges'],
            plate_location_input['copy_plate_ranges'],
            'plate ranges not equal: %r - %r' % (
                new_obj['copy_plate_ranges'],
                plate_location_input['copy_plate_ranges']))
        
        # Test ApiLogs:
        # plate - one for each plate
        # plate_location - one for each plate addition to the range
        
    def test4_create_library_invalids(self):
        ''' Test Library schema "required" validations'''
         
        logger.info('test create_library_invalids...')
        library_resource = self.get_resource_from_server('library')
        fields = library_resource['fields']
        resource_uri = BASE_URI_DB + '/library'
        for key,field in fields.items():
            if field.get('required', False):
                logger.debug('testing required field: %r, %r', key, field)
                
                library_item = LibraryFactory.attributes()
                library_item[key] = None
                resp = self.api_client.post(
                    resource_uri, format='json', data=library_item, 
                    authentication=self.get_credentials())
                self.assertTrue(
                    resp.status_code in [400], 
                    (resp.status_code, self.get_content(resp)))
        
                obj = self.deserialize(resp)
                self.assertTrue(find_in_dict(key, obj), 
                    'Error: response error not found: %r, obj: %r' %(key, obj))

        # Test invalid Library name                
        library_item = LibraryFactory.attributes()
        library_item['library_name'] = 'invalid & name'
        resp = self.api_client.post(
            resource_uri, format='json', data=library_item, 
            authentication=self.get_credentials())
        
        self.assertTrue(
            resp.status_code in [400], 
            (resp.status_code, self.get_content(resp)))
        
        logger.debug('response.content.library message: %r', 
                         getattr(resp, 'content'))
        
        key = 'library_name'
        obj = self.deserialize(resp)
        self.assertTrue(find_in_dict(key, obj), 
            'Error: response error not found: %r, obj: %r' %(key, obj))

        # Test invalid Library type
        library_item = LibraryFactory.attributes()
        library_item['library_type'] = 'invalid_type'
        resp = self.api_client.post(
            resource_uri, format='json', data=library_item, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [400], 
            (resp.status_code, self.get_content(resp)))
        key = 'library_type'
        obj = self.deserialize(resp)
        self.assertTrue(find_in_dict(key, obj), 
            'Error: response error not found: %r, obj: %r' %(key, obj))

        # TODO: test regex and number: min/max, vocabularies
        
    def test61_small_molecule_delete_compound_name(self):
        # TODO: test that sm.compound names can be removed
        pass
    
    def test6_load_small_molecule_file(self):
        
        logger.info('test6_load_small_molecule_file')
        
        library_item = self.create_library({ 
            'start_plate': '1536', 
            'end_plate': '1536', 
            'plate_size': '384',
            'screen_type': 'small_molecule' })

        resource_name = 'well'
        resource_uri = '/'.join([
            BASE_URI_DB,'library', library_item['short_name'],resource_name])
        filename = (
            '%s/db/static/test_data/libraries/clean_data_small_molecule.sdf'
                % APP_ROOT_DIR )

        data_for_get = { 'limit': 0, 'includes': ['*'] }
        data_for_get['HTTP_ACCEPT'] = SDF_MIMETYPE

        with open(filename) as input_file:
            
            input_data = self.serializer.from_sdf(input_file.read())
            input_data = input_data['objects']
            expected_count = 8
            self.assertEqual(
                len(input_data), expected_count, 
                str(('initial serialization of ',filename,'found',
                    len(input_data), 'expected',expected_count,
                    'input_data',input_data)))
            
            logger.debug('======Submitting patch file: %r, uri: %r ...', 
                filename, resource_uri)
            resp = self.api_client.put(
                resource_uri, format='sdf', data=input_data, 
                authentication=self.get_credentials(), **data_for_get )
            self.assertTrue(
                resp.status_code in [200], 
                (resp.status_code, self.get_content(resp)))
        
        # Examine Well/Reagents created:
        # NOTE: get the library "reagents" instead of the wells: reagents have 
        # one-to-many reln to wells, so the well endpoint returns only first;
        # also, the well resource is not the linked resource type, and does not
        # have the reagent fields.  
        resource_name = 'reagent'
        reagent_resource_uri = '/'.join([
            BASE_URI_DB,'library', library_item['short_name'],resource_name])
        resp = self.api_client.get(
            reagent_resource_uri, format='sdf', 
            authentication=self.get_credentials(), 
            data=data_for_get)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        returned_data = new_obj['objects']
        expected_count = 384
        self.assertEqual(
            len(returned_data), expected_count, 
            ('returned_data of ',filename,'found',
                len(returned_data), 'expected',expected_count,
                'returned_data',returned_data))

        specific_schema = self.get_from_server(resource_uri + '/schema')
        fields = specific_schema['fields']
        self.validate_wells(input_data, returned_data, fields)
                    
        # Test 2: update some wells, check results, and api logs
        filename = ( APP_ROOT_DIR 
            + '/db/static/test_data/libraries/clean_data_small_molecule_update.sdf')

        data_for_get = { 'limit': 0, 'includes': ['*', '-structure_image'] }
        data_for_get['HTTP_ACCEPT'] = SDF_MIMETYPE

        with open(filename) as input_file:
            
            input_data = self.serializer.from_sdf(input_file.read())
            input_data = input_data['objects']
            expected_count = 4
            self.assertEqual(
                len(input_data), expected_count, 
                str(('initial serialization of ',filename,'found',
                    len(input_data), 'expected',expected_count,
                    'input_data',input_data)))
            logger.info('PATCH: %r ...', resource_uri)
            resp = self.api_client.patch(
                resource_uri, format='sdf', data=input_data, 
                authentication=self.get_credentials(), **data_for_get )
            self.assertTrue(
                resp.status_code in [200], 
                (resp.status_code, self.get_content(resp)))
        
            resource_name = 'well'
            resource_uri = '/'.join([
                BASE_URI_DB,'library', library_item['short_name'],
                resource_name])
            resp = self.api_client.get(
                reagent_resource_uri, format='sdf', 
                authentication=self.get_credentials(), 
                data=data_for_get)
            self.assertTrue(
                resp.status_code in [200], 
                (resp.status_code, self.get_content(resp)))
            new_obj = self.deserialize(resp)
            returned_data = new_obj['objects']
            expected_count = 384
            self.assertEqual(
                len(returned_data), expected_count, 
                ('returned_data of ',filename,'found',
                    len(returned_data), 'expected',expected_count,
                    'returned_data',returned_data))
        
            # 1. test the specific wells
            well_ids_to_check = input_data
    
            for update_well in well_ids_to_check:
                search = { 
                    'well_name': update_well['well_name'], 
                    'plate_number': update_well['plate_number']}
                result, outputobj = find_obj_in_list(
                    search,returned_data) #, excludes=excludes )
                self.assertTrue(
                    result, 
                    ('not found', search,outputobj,'=== objects returned ===', 
                          returned_data ) ) 
                result, msgs = assert_obj1_to_obj2(update_well, outputobj)
                self.assertTrue(result, (msgs, update_well, outputobj))
                self.assertTrue(
                    'library_well_type' in outputobj,
                    'library_well_type is missing in %r' % outputobj)
                self.assertTrue(
                    outputobj['library_well_type']=='experimental',
                    'wrong library_well_type: %r' % outputobj)
            
            # 2. check the apilogs - library
            resource_uri = BASE_REPORTS_URI + '/apilog'
            resp = self.api_client.get(
                resource_uri, format='json', 
                authentication=self.get_credentials(), 
                data={ 
                    'limit': 0, 
                    'ref_resource_name': 'library', 
                    'key': library_item['short_name'] })
            self.assertTrue(
                resp.status_code in [200], 
                (resp.status_code, self.get_content(resp)))
            new_obj = self.deserialize(resp)
            expected_count = 3 # create, post, update
            self.assertEqual( 
                len(new_obj['objects']), expected_count , 
                str((len(new_obj['objects']), expected_count, new_obj)))

            # 3. check the apilogs - wells
            resource_uri = BASE_REPORTS_URI + '/apilog' 
            resp = self.api_client.get(
                resource_uri, format='json', 
                authentication=self.get_credentials(), 
                data={ 'limit': 0, 'ref_resource_name': 'well' })
            self.assertTrue(
                resp.status_code in [200], 
                (resp.status_code, self.get_content(resp)))
            new_obj = self.deserialize(resp)
            logs = new_obj['objects']
            # (none for create), 4 for update
            expected_count = 4
            self.assertEqual( 
                len(logs), expected_count , 
                str((len(logs), expected_count)))
            
            self.assertEqual(
                len(logs), 4, 
                ('expected %d patch logs, found: %d' %(4,len(logs)), logs))
            
            for log in logs:
                self.assertTrue(
                    ( 'parent_log_uri' in log and
                      library_item['short_name'] in log['parent_log_uri'] ), 
                    'parent_log_uri should contain: %r - %r' 
                    % (library_item['short_name'], log) )
                if log['key'] == '01536:A01':
                    logger.info('log: %r', log)
                    self.assertTrue(
                        set(log['diff_keys'])==
                        set([
                            "pubchem_cid", "vendor_identifier", 
                            "vendor_batch_id", "compound_name", "smiles"]),
                        'diff_keys: %r should equal %r' % (
                            log['diff_keys'],[
                                "pubchem_cid", "vendor_identifier", 
                                "vendor_batch_id", "compound_name", "smiles"]))
            # TODO: check parent_log - library log/ version
    
    def test7_load_sirnai(self):

        logger.info('test7_load_sirnai ...')
        
        filename = ('%s/db/static/test_data/libraries/clean_data_rnai.xlsx'
                    % APP_ROOT_DIR )
        
        library_item = self.create_library({ 
            'start_plate': 50001,  
            'end_plate': 50001, 
            'plate_size': '384',
            'screen_type': 'rnai' })
        resource_uri = BASE_URI_DB + '/library'
        
        self._load_xls_reagent_file(filename,library_item, 5,384 )

    # def test7a_sirna_validations(self):
    #     # TODO: test validations
    #     pass

    def test8_sirna_duplex(self):
        
        logger.info('test8_sirna_duplex ...')
        
        filename = (  
            '%s/db/static/test_data/libraries/clean_rnai_duplex_50440_50443.xlsx'
            % APP_ROOT_DIR)
        
        # create the duplex library
        library_item = self.create_library({ 
            'start_plate': 50440,  
            'end_plate': 50443, 
            'plate_size': '384',
            'screen_type': 'rnai' })
        
        self._load_xls_reagent_file(filename,library_item, 532, 1536 )
        filename = ( '%s/db/static/test_data/libraries/clean_rnai_pool.xlsx'
                     % APP_ROOT_DIR )
        # create the pool library
        library_item = self.create_library({ 
            'start_plate': 50439,  
            'end_plate': 50439, 
            'plate_size': '384',
            'screen_type': 'rnai',
            'is_pool': True })
        
        self._load_xls_reagent_file(filename,library_item, 133, 384 )
        
        ## TODO: test the duplex wells are set on the pool well
        
    def test9_natural_product(self):
        
        logger.info('test9_natural_product ...')
        
        filename = (  
            '%s/db/static/test_data/libraries/clean_data_natural_product.xlsx'
            % APP_ROOT_DIR )
        
        library_item = self.create_library({ 
            'start_plate': 2037,  
            'end_plate': 2037, 
            'plate_size': '384',
            'library_type': 'natural_products' })
        
        self._load_xls_reagent_file(filename,library_item, 352, 384 )

    def _load_xls_reagent_file(
            self,filename,library_item, expected_in, expected_count):
        ''' Test the loading of an xls well/reagent file''' 

        start_plate = library_item['start_plate']
        end_plate = library_item['end_plate']

        resource_name = 'well'
        resource_uri = '/'.join([
            BASE_URI_DB,'library', library_item['short_name'],resource_name])
        
        data_for_get = { 'limit': 0, 'includes': ['*', '-structure_image'] }
        data_for_get['HTTP_ACCEPT'] = XLSX_MIMETYPE
        xls_serializer = XLSSerializer()
        
        with open(filename) as input_file:
            ### NOTE: submit the data as an object, because the test framework
            ### will convert it to the target format.
            input_data = self.serializer.from_xlsx(input_file.read())
            input_data = [x for x in input_data['objects']]
            self.assertEqual(
                len(input_data), expected_in, 
                str(('initial serialization of ',filename,'found',
                    len(input_data), 'expected',expected_in)))
            
            resp = self.api_client.put(
                resource_uri, format='xlsx', data=input_data, 
                authentication=self.get_credentials(), **data_for_get )
            self.assertTrue(
                resp.status_code in [200, 204], 
                (resp.status_code, 
                 self.get_content(resp)))
        
        resource_name = 'reagent'
        reagent_resource_uri = '/'.join([
            BASE_URI_DB,'library', library_item['short_name'],resource_name])
        resp = self.api_client.get(
            reagent_resource_uri, format='xlsx', 
            authentication=self.get_credentials(), 
            data=data_for_get)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        returned_data = [x for x in new_obj['objects']]
        self.assertEqual(
            len(returned_data), expected_count, 
            str(('returned_data of ',filename,'found',
                len(returned_data), 'expected',expected_count )))

        # 1. test well keys
        specific_schema = self.get_from_server(resource_uri + '/schema')
        fields = specific_schema['fields']
        
        self.validate_wells(input_data, returned_data, fields)
        
        # TODO: test substance IDs    
        # substance_ids = set()
        # for inputobj in input_data:
        #     # first just search for the well
        #     search = { 
        #         'well_id': lims_utils.well_id(
        #             inputobj['plate_number'],inputobj['well_name']) }
        #     result, outputobj = find_obj_in_list(search,returned_data)
        #     self.assertTrue(
        #         result, 
        #         ('not found', search,outputobj,'=== objects returned ===', 
        #               returned_data )) 
        # 
        #     # second look at the found item
        #     expected_data = { key: inputobj[key] for key in fields.keys() 
        #         if key in inputobj }
        #     logger.debug('checking: expected: %r', expected_data )
        #     result, msgs = assert_obj1_to_obj2(expected_data, outputobj)
        #     self.assertTrue(result, (msgs, expected_data, outputobj))
        #      
        #     substance_id = outputobj['substance_id']
        #     self.assertTrue(
        #         substance_id not in substance_ids, 
        #         ('substance_id not unique', substance_id, substance_ids))
        #     substance_ids.add(substance_id)
                    

class ScreenResultSerializerTest(TestCase):
    ''' Test serialization of the ScreenResult file'''
    
    def test1_read_write(self):
        
        serializer = ScreenResultSerializer()
        file = 'ScreenResultTest_1_valid.xlsx'
        file_out = 'ScreenResultTest_1_valid_out.xlsx'
        filename = '%s/db/static/test_data/screens/%s' %(APP_ROOT_DIR,file)
        filename_out = (
            '%s/db/static/test_data/screens/%s' %(APP_ROOT_DIR,file_out) )
        
        with open(filename, 'rb') as input_file:
            wb = xlrd.open_workbook(file_contents=input_file.read())
            input_data = screen_result_importer.read_workbook(wb)
            logger.info('input_data: keys: %r', input_data.keys())
            
        lookup_key = 'meta'
        self.assertTrue(lookup_key in input_data, 
            '%r not in input_data: %r' % (lookup_key, input_data.keys()))
        meta = input_data[lookup_key]
        lookup_key = 'screen_facility_id'
        self.assertTrue(lookup_key in meta, 
            '%r not in meta: %r' % (lookup_key, meta))
        screen_facility_id = meta[lookup_key]
        
        lookup_key = 'fields'
        self.assertTrue(lookup_key in input_data, 
            '%r not in input_data: %r' % (lookup_key, input_data.keys()))
        data_columns = input_data[lookup_key]
        logger.info('data colums: %r', data_columns.keys())
        
        lookup_key = 'objects'
        self.assertTrue(lookup_key in input_data, 
            '%r not in input_data: %r' % (lookup_key, input_data.keys()))
        result_values = input_data[lookup_key]
        with open(filename_out, 'wb') as output_file:
            logger.info('write back out to %r', filename_out)
            data = screen_result_importer.create_output_data(
                screen_facility_id, data_columns, result_values)
            output_file.write(serializer.to_xlsx(data))
                
        with open(filename_out, 'rb') as output_file_in:
            try:
                logger.info('read back in from output file: %r', filename_out)
                output_data = serializer.from_xlsx(output_file_in.read())
                logger.info('output_data: keys: %r', output_data.keys())
            except ValidationError,e:
                logger.warn('validation error: %r', e.errors)
                raise
        self.validate(self, input_data, output_data)
        
    def test2_errors(self):
        ''' Test ScreenResult file format errors'''
        
        serializer = ScreenResultSerializer()
        file = 'ScreenResultParseErrorsTest.xlsx'
        file_out = 'ScreenResultParseErrorsTest_out.xlsx'
        filename = '%s/db/static/test_data/screens/%s' %(APP_ROOT_DIR,file)
        filename_out = (
            '%s/db/static/test_data/screens/%s' %(APP_ROOT_DIR,file_out) )
        logger.info('check file: %r ', os.path.exists(filename))
        with open(filename, 'rb') as input_file:
            try:
                input_data = serializer.from_xlsx(input_file.read())
                
                # Iterate some of the result values to trigger the 
                # validation error
                for rv in input_data['objects']:
                    logger.info('rv: %r', rv)
                    self.fail(
                        'error workbook should generate errors exception: %r'
                        % filename)
            except ValidationError, e:
                logger.info('errors reported: %r', e.errors)
                self.assertTrue(
                    len(e.errors.keys())==3, 
                    'should be 3 errors, one for each plate sheet without '
                    'a "plate" field')
                
    @staticmethod
    def validate(testinstance,input_data,output_data):
        ''' Validate ScreenResult serialization round trip '''
        
        logger.info('validate input/output data')
        logger.info('input_data: keys: %r', input_data.keys())
        logger.info('output_data: keys: %r', output_data.keys())
        logger.info(
            'input_data: field keys: %r', input_data['fields'].keys())
        logger.info(
            'output_data: field keys: %r', output_data['fields'].keys())
                    
        # Test DataColumns
        input_fields = input_data['fields']
        output_fields = output_data['fields']
        testinstance.assertTrue(
            len(input_fields)==len(output_fields),
            'input/output fields: %r, %r' 
            % (input_fields.keys(), output_fields.keys()))    
        for colname,input_field in input_fields.items():
            found = None
            for output_field in output_fields.values():
                if input_field['name'] == output_field['name']:
                    found = output_field
                    break
            testinstance.assertTrue(
                found is not None, 
                'input datacolumn not found: %r, output datacolumns: %r'
                % (input_field, output_fields))

            for column_field, val in input_field.items():
                val = input_field[column_field]
                val2 = output_field[column_field]
                if val and column_field == 'derived_from_columns':
                    val = set([x.upper() for x in re.split(r'[,\s]+', val)])
                    val2 = set([x.upper() for x in re.split(r'[,\s]+', val2)])
                    
                result,msg = equivocal(val, val2)
                testinstance.assertTrue(
                    result,
                    'column not equal: column %r:%r, input: %r, output: %r, %r'
                    % (colname,column_field, val, val2, msg))
        
        # test ResultValues
        items_to_test = 1000
        try:
            # save output data generator to a list
            output_list = [x for x in output_data['objects']]
        except Exception, e:
            logger.exception('%r', e)
            raise
        for i,result_value in enumerate(input_data['objects']):
            found = None
            for result_value_out in output_list:
                logger.debug('try: %r - %r', result_value,result_value_out)
                if result_value['well_id'] == result_value_out['well_id']:
                    found = result_value_out
                    break;
                logger.debug('not matched')
            if not found:
                testinstance.fail(
                    'result value well_id not found: %r in %r' 
                    % (result_value['well_id'], [x for x in output_list]))
            for key,val in result_value.items():
                testinstance.assertTrue(
                    key in found, 
                    ('key: %r, from input: %r,'
                        ' not found in output result_value: %r') 
                    % (key, result_value, found) )

                val2 = found[key]
                if val2 == 'NP':
                    testinstance.assertTrue(val=='NP' or val==None,
                        ('partition positive val: %r, key: %r, %r - %r'
                            % (val, key, result_value, found)))
                elif val2 == 'NT':
                    testinstance.assertTrue(val=='NT' or val==None,
                        ('confirmed positive val: %r, key: %r, %r - %r'
                            % (val, key, result_value, found)))
                else:
                    result,msg = equivocal(val, val2)
                    testinstance.assertTrue(
                        result,
                        ('meta field not equal: %r %r != %r, %r'
                             'input: %r, output: %r')
                        % (key, val, val2, msg, result_value, found))
            if i == items_to_test:
                break
                
class ScreenResultResource(DBResourceTestCase):

    def __init__(self,*args,**kwargs):
    
        super(DBResourceTestCase, self).__init__(*args,**kwargs)
        self.sr_serializer = ScreenResultSerializer()
        self.sr_api_client = TestApiClient(serializer=self.sr_serializer)       

    def setUp(self):

        super(ScreenResultResource, self).setUp()

    def tearDown(self):
        
        DBResourceTestCase.tearDown(self)
        logger.info('delete resources')
        with connection.cursor() as cursor:
            try:
                cursor.execute('delete from well_query_index;')
            except Exception as e:
                logger.exception('on delete well_query_index')
            try:
                cursor.execute('delete from well_data_column_positive_index;')
            except Exception as e:
                logger.exception('on delete well_data_column_positive_index')
            try:
                cursor.execute('delete from cached_query;')
            except Exception as e:
                logger.exception('on delete cached_query')
        Screen.objects.all().delete()
        Library.objects.all().delete()
        ApiLog.objects.all().delete()
        ScreensaverUser.objects.all().exclude(username='testsuper').delete()

        # ScreensaverUser.objects.all().filter(username='adminuser').delete()

    def _setup_test_config(self):

        # Setup ScreenResult dependencies
        
        logger.info('create library...')
        library1 = self.create_library({
            'start_plate': 1, 
            'end_plate': 20,
            'screen_type': 'small_molecule' })

        logger.info('create and load well data...')
        plate = 1
        input_data = [
            self.create_small_molecule_test_well(
                plate,i,library_well_type='experimental') 
            for i in range(0,20)]
        # setup one control well
        input_data.append(
            self.create_small_molecule_test_well(
                plate,20,library_well_type='empty') 
            )
        resource_name = 'well'
        resource_uri = '/'.join([
            BASE_URI_DB,'library', library1['short_name'],resource_name])
        resp = self.api_client.put(
            resource_uri, format='sdf', data={ 'objects': input_data } , 
            authentication=self.get_credentials(), 
            **{ 'limit': 0, 'includes': '*'} )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))

    def test1_load_example_file(self):
        
        logger.info('test1_load_example_file...')
        default_data_for_get = { 'limit': 0, 'includes': ['*'] }
        default_data_for_get['HTTP_AUTHORIZATION'] = self.get_credentials()
        
        logger.info('create library...')
        library1 = self.create_library({
            'start_plate': 1, 
            'end_plate': 20,
            'screen_type': 'small_molecule' })

        logger.info('create screen...')        
        screen = self.create_screen({ 'screen_type': 'small_molecule' })

        logger.info('create and load well data...')
        input_data = []
        for plate in range(1,4):
            for i in range(0,384):
                if i in [20,21,22]:
                    # setup control wells
                    input_data.append(self.create_small_molecule_test_well(
                        plate,i,library_well_type='empty'))
                else:
                    input_data.append(self.create_small_molecule_test_well(
                        plate,i,library_well_type='experimental'))
        resource_name = 'well'
        resource_uri = '/'.join([
            BASE_URI_DB,'library', library1['short_name'],resource_name])
        data_for_get = {}
        data_for_get.update(default_data_for_get)
        data_for_get['CONTENT_TYPE'] = JSON_MIMETYPE
        logger.info('PUT library well data ...')
        resp = self.django_client.put(
            resource_uri, data=json.dumps({ 'objects': input_data }) ,
            **data_for_get)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        
        data_for_get = {}
        data_for_get.update(default_data_for_get)
        data_for_get['CONTENT_TYPE'] = XLSX_MIMETYPE
        data_for_get['HTTP_ACCEPT'] = XLSX_MIMETYPE
        
        file = 'ScreenResultTest_1_valid.xlsx'
        filename = '%s/db/static/test_data/screens/%s' %(APP_ROOT_DIR,file)
        with open(filename) as input_file:

            resource_uri = '/'.join([
                BASE_URI_DB,'screenresult',screen['facility_id']])
            logger.info('PUT screen result to the server...')
            django_test_client = self.api_client.client
            resp = django_test_client.post(
                resource_uri, content_type='application/xls', 
                data=input_file.read(), **data_for_get)
            if resp.status_code not in [200, 204]:
                content = self.deserialize(resp)
                logger.info('content: %r', content)
                logger.info('resp: %r', 
                    [str(x) for x in content])
            self.assertTrue(
                resp.status_code in [200, 204], 
                (resp.status_code))
            input_file.seek(0)
            input_data = self.sr_serializer.from_xlsx(input_file.read())            

        logger.info('screen result loaded, refetch from server...')

        # TODO: replace all tastypie.test.TestApiClient clients with the
        # django.test.client.Client instances:
        # Tastypie mucks with the HEADERS and uses non-standard "format" arg:
        # resp = self.sr_api_client.get(
        #     resource_uri, authentication=self.get_credentials(), 
        #     format='xlsx', **data_for_get)
        
        resp = self.django_client.get(
            resource_uri, authentication=self.get_credentials(), 
            **data_for_get)
        self.assertTrue(
            resp.status_code in [200,201,202], 
            (resp.status_code, self.get_content(resp)))
        output_data = self.sr_serializer.deserialize(
            self.get_content(resp), XLSX_MIMETYPE)
        
        ScreenResultSerializerTest.validate(self, input_data, output_data)    
    
    def _create_valid_input(self, screen_facility_id):
        # create data as already parsed input
        # TODO: rework the screenresult import into a generic serialization:
        # fields: keyed by (unique) datacolumn name
        # result_values: as an array of dicts (using a generator)
        # result_values: dropt the "numeric_value/value"        
        fields = {
            'E': {
                'ordinal': 0,
                'name': 'Field1',
                'data_worksheet_column': 'E',
                'data_type': 'text', 
                'description': 'field 1 description',
                'replicate_ordinal': 1,
            },
            'F': {
                'ordinal': 1,
                'name': 'Field2',
                'data_worksheet_column': 'F',
                'data_type': 'numeric',
                'decimal_places': 2, 
                'description': 'field 2 description',
                'replicate_ordinal': 1,
                'is_follow_up_data': True,
                'assay_readout_type': 'luminescence',
            },
            'G': {
                'ordinal': 2,
                'name': 'Field3',
                'data_worksheet_column': 'G',
                'data_type': 'numeric',
                'decimal_places': 4, 
                'description': 'field 3 description',
                'replicate_ordinal': 2,
                'is_follow_up_data': True,
                'assay_readout_type': 'flourescence_intensity',
            },
            'H': {
                'ordinal': 3,
                'name': 'Field4',
                'data_worksheet_column': 'H',
                'data_type': 'confirmed_positive_indicator',
                'description': 'field 4 description',
                'how_derived': 'z-score > 1 for Field3'
            },
            'I': {
                'ordinal': 4,
                'name': 'Field5',
                'data_worksheet_column': 'I',
                'data_type': 'partition_positive_indicator',
                'description': 'field 5 description',
                'how_derived': 'ranking of z-score <.5, ,.5<=x<=1, >1'
            },
        }
        result_values = [
            { 
                'well_id': '00001:A01', 
                'E': 'test value 1',
                'F': 91.19 ,
                'G': .0011 ,
                'H': None ,  # should be interpreted as 'NT'
                'I': None , # should be interpreted as 'NP'
            },
            { 
                'well_id': '00001:A02', 
                'E': 'test value 2',
                'F': 0.99 ,
                'G': 1.0331 ,
                'H': 'CP',
                'I': 'W' ,
            },
            { 
                'well_id': '00001:A03', 
                'E': 'test value 2',
                'F': 1.99 ,
                'G': 1.032 ,
                'H': 'I',
                'I': 'M' ,
            },
            { 
                'well_id': '00001:A04', 
                'E': 'test value 2',
                'F': 1.99 ,
                'G': 1.032 ,
                'H': 'NT',
                'I': 'S' ,
            },
            { 
                'well_id': '00001:A05', 
                'E': 'test value 2',
                'F': 1.1 ,
                'G': 1.1 ,
                'H': 'CP',
                'I': 'M' ,
            },
            { 
                'well_id': '00001:A06', 
                'E': 'test value 2',
                'F': 1.1 ,
                'G': 1.1 ,
                'H': 'FP',
                'I': 'M' ,
            },
            { 
                'well_id': '00001:A21',
                'assay_well_control_type': 'assay_control', 
                'E': 'test value 2',
                'F': 1.1 ,
                'G': 1.1 ,
                'H': 'FP', # non experimental well should be ignored
                'I': 'M' , # non experimental well should be ignored
            },
            { 
                'well_id': '00001:A07',
                'exclude': ['E','F','G','H','I'], 
                'E': 'test value 2',
                'F': 1.1 ,
                'G': 1.1 ,
                'H': 'FP',
                'I': 'M' ,
            },
        ]
        input_data = OrderedDict((
            ('meta', {'screen_facility_id': screen_facility_id } ),
            ('fields', fields),
            ('objects', result_values),
        ))
        return input_data
    
    
    def test2_load_valid_input(self):
        
        logger.info('test2_load_valid_input...')
        default_data_for_get = { 'limit': 0, 'includes': ['*'] }
        default_data_for_get['HTTP_AUTHORIZATION'] = self.get_credentials()
        
        self._setup_test_config()
        logger.info('create screen...')        
        screen = self.create_screen({ 'screen_type': 'small_molecule' })
        screen_facility_id = screen['facility_id']
        logger.info('created screen %r', screen_facility_id)        
        
        input_data = self._create_valid_input(screen_facility_id)

        # The ScreenResultSerializer only recognizes the XLSX format:
        # So serialize the input data into an XLSX file
        input_data_put = screen_result_importer.create_output_data(
            screen_facility_id, 
            input_data['fields'], 
            input_data['objects'] )
        input_data_put = self.sr_serializer.serialize(
            input_data_put, XLSX_MIMETYPE)
        data_for_get = {}
        data_for_get.update(default_data_for_get)
        data_for_get['CONTENT_TYPE'] = XLSX_MIMETYPE
        data_for_get['HTTP_ACCEPT'] = XLSX_MIMETYPE
        logger.info('PUT screen result to the server...')
        screen_facility_id = screen['facility_id']
        resource_name = 'screenresult'
        resource_uri = '/'.join([
            BASE_URI_DB,resource_name,screen_facility_id])
        resp = self.django_client.put(
            resource_uri, data=input_data_put, **data_for_get )
        # resp = self.sr_api_client.put(
        #     resource_uri, format='xlsx', data=input_data_put, 
        #     authentication=self.get_credentials(), **data_for_get )
        if resp.status_code not in [200, 204]:
            content = self.get_content(resp)
            if content:
                logger.info('resp: %r', 
                    [[str(y) for y in x] 
                        for x in self.serializer.from_xlsx(content)])
        self.assertTrue(
            resp.status_code in [200, 204], resp.status_code)

        logger.info('refetch screen result from server...')
        resp = self.django_client.get(resource_uri, **data_for_get)
        # resp = self.sr_api_client.get(
        #     resource_uri, authentication=self.get_credentials(), 
        #     format='xlsx', **data_for_get)
        if resp.status_code not in [200, 204]:
            content = self.get_content(resp)
            if content:
                logger.info('resp: %r', 
                    [[str(y) for y in x] 
                        for x in self.serializer.from_xlsx(content)])
        self.assertTrue(resp.status_code in [200,201,202],resp.status_code)
        output_data = self.sr_serializer.deserialize(
            self.get_content(resp), XLSX_MIMETYPE)
        ScreenResultSerializerTest.validate(self, input_data, output_data)
        
        # Test statistics:
        # result value count
        # experimental wells, replicates, etc
        
        screen = self.get_screen(screen['facility_id'])
        
        key = 'experimental_well_count'
        expected_value = 7
        self.assertTrue(screen[key]==expected_value,
            (key,'expected_value',expected_value,
                'returned value',screen[key]))
        key = 'library_plates_data_loaded'
        expected_value = 1
        self.assertTrue(screen[key]==expected_value,
            (key,'expected_value',expected_value,
                'returned value',screen[key]))
        key = 'min_data_loaded_replicate_count'
        expected_value = 2
        self.assertTrue(screen[key]==expected_value,
            (key,'expected_value',expected_value,
                'returned value',screen[key]))
        key = 'max_data_loaded_replicate_count'        
        expected_value = 2
        self.assertTrue(screen[key]==expected_value,
            (key,'expected_value',expected_value,
                'returned value',screen[key]))

        key = 'assay_readout_types'
        expected_value = ['luminescence', 'flourescence_intensity']
        self.assertTrue(set(screen[key]) <= set(expected_value),
            (key,'expected_value',expected_value,
                'returned value',screen[key]))

        # Test Datacolumn positive counts
        
        resource_name = 'datacolumn'
        resource_uri = '/'.join([BASE_URI_DB,resource_name])
        data_for_get = {
            'screen_facility_id__eq': screen_facility_id,
            'includes': '*',
            'data_type__in': [
              'partition_positive_indicator','boolean_positive_indicator',
              'confirmed_positive_indicator'],
            'order_by': ['ordinal']
        }
        data_for_get.setdefault('limit', 0)
        resp = self.api_client.get(
            resource_uri, authentication=self.get_credentials(), 
            format='json', data=data_for_get)
        self.assertTrue(
            resp.status_code in [200,201,202], 
            (resp.status_code, self.get_content(resp)))
        output_data = self.deserialize(resp)
        logger.debug('datacolumn output_data: %r', output_data )
        self.assertTrue(len(output_data['objects'])==2, 
            ('should show two positive indicator columns', output_data))

        confirmed_positive_col = None
        partion_positive_col = None
        for col in output_data['objects']:
            if col['data_type'] == 'confirmed_positive_indicator':
                confirmed_positive_col = col
            if col['data_type'] == 'partition_positive_indicator':
                partion_positive_col = col
        self.assertTrue(confirmed_positive_col is not None, 
            ('confirmed_positive_col not found in %r',output_data))
        self.assertTrue(partion_positive_col is not None, 
            ('partion_positive_col not found in %r',output_data))
        
        key = 'positives_count'
        expected_value=2
        self.assertTrue(confirmed_positive_col[key]==expected_value,
            (key,'expected_value',expected_value,
                'returned value',confirmed_positive_col[key], 
                'col', confirmed_positive_col))
        key = 'positives_count'
        expected_value=5
        self.assertTrue(partion_positive_col[key]==expected_value,
            (key,'expected_value',expected_value,
                'returned value',partion_positive_col[key]))
        key = 'strong_positives_count'
        expected_value=1
        self.assertTrue(partion_positive_col[key]==expected_value,
            (key,'expected_value',expected_value,
                'returned value',partion_positive_col[key]))
        key = 'medium_positives_count'
        expected_value=3
        self.assertTrue(partion_positive_col[key]==expected_value,
            (key,'expected_value',expected_value,
                'returned value',partion_positive_col[key]))
        key = 'weak_positives_count'
        expected_value=1
        self.assertTrue(partion_positive_col[key]==expected_value,
            (key,'expected_value',expected_value,
                'returned value',partion_positive_col[key]))

        logger.info('test2_load_valid_input, done.')        

    def test3_mutual_positives(self):

        logger.info('test3_mutual_positives...')
        default_data_for_get = { 'limit': 0, 'includes': ['*'] }
        default_data_for_get['HTTP_AUTHORIZATION'] = self.get_credentials()
        
        self.test2_load_valid_input()
         
        # create another screen with the same input
        logger.info('create screen...')        
        screen = self.create_screen({ 'screen_type': 'small_molecule' })
        screen_facility_id = screen['facility_id']
        logger.info('created screen %r', screen_facility_id)        
        input_data = self._create_valid_input(screen_facility_id)

        # The ScreenResultSerializer only recognizes the XLSX format:
        # So serialize the input data into an XLSX file
        input_data_put = screen_result_importer.create_output_data(
            screen_facility_id, 
            input_data['fields'], 
            input_data['objects'] )
        input_data_put = self.sr_serializer.serialize(
            input_data_put, XLSX_MIMETYPE)
        data_for_get = {}
        data_for_get.update(default_data_for_get)
        data_for_get['CONTENT_TYPE'] = XLSX_MIMETYPE
        data_for_get['HTTP_ACCEPT'] = XLSX_MIMETYPE
        logger.info('PUT screen result to the server...')
        screen_facility_id = screen['facility_id']
        resource_name = 'screenresult'
        resource_uri = '/'.join([
            BASE_URI_DB,resource_name,screen_facility_id])
        resp = self.django_client.put(
            resource_uri, data=input_data_put, **data_for_get )
        # resp = self.sr_api_client.put(
        #     resource_uri, format='xlsx', data=input_data_put, 
        #     authentication=self.get_credentials(), **data_for_get )
        if resp.status_code not in [200, 204]:
            content = self.get_content(resp)
            if content:
                logger.info('resp: %r', 
                    [[str(y) for y in x] 
                        for x in self.serializer.from_xlsx(content)])
        self.assertTrue(
            resp.status_code in [200, 204], resp.status_code)

        logger.info('refetch screen result from server...')
        resp = self.django_client.get(resource_uri, **data_for_get)
        # resp = self.sr_api_client.get(
        #     resource_uri, authentication=self.get_credentials(), 
        #     format='xlsx', **data_for_get)
        if resp.status_code not in [200, 204]:
            content = self.get_content(resp)
            if content:
                logger.info('resp: %r', 
                    [[str(y) for y in x] 
                        for x in self.serializer.from_xlsx(content)])
        self.assertTrue(resp.status_code in [200,201,202],resp.status_code)
        output_data = self.sr_serializer.deserialize(
            self.get_content(resp), XLSX_MIMETYPE)
        ScreenResultSerializerTest.validate(self, input_data, output_data)
        
        # verify that the screen1 mutual columns = screen 2 mutual columns
        screen_facility_id = screen['facility_id']
        resource_name = 'screenresult'
        resource_uri = '/'.join([
            BASE_URI_DB,resource_name,screen_facility_id])
         # use JSON to retrieve the data, mutual positives columns would be 
         # ignored when deserializing from XLSX
        data_for_get = {}
        data_for_get.update(default_data_for_get)
        data_for_get['HTTP_ACCEPT'] = JSON_MIMETYPE
        data = {}
        data['includes'] = '*'
        data['show_mutual_positives'] = True
        logger.info('get the screen result: %r', data_for_get)
        resp = self.django_client.get(resource_uri, data=data, **data_for_get)
        if resp.status_code not in [200, 204]:
            content = self.get_content(resp)
            if content:
                logger.info('resp: %r', 
                    [[str(y) for y in x] 
                        for x in self.serializer.from_xlsx(content)])
        self.assertTrue(resp.status_code in [200,201,202],resp.status_code)
        logger.info('deserialize to json and check mutual positives columns...')
        # use generic deserialization to show all of the fields
        content = self.get_content(resp)
        output_data = self.serializer.deserialize(
            content, JSON_MIMETYPE)
        
        try:
            # save output data generator to a list
            output_list = [x for x in output_data['objects']]
        except Exception, e:
            logger.exception('%r', e)
            raise
        self.assertTrue(len(output_list)>0, 
            'no screen results in output: %r' % output_data)
        logger.info((
            'verify that mutual positive columns are in the result value '
            'fields: %r'), output_list[0] )
        mutual_positive_cols_screen_1 = ['dc_1_field4', 'dc_1_field5']
        for col in mutual_positive_cols_screen_1:
            self.assertTrue(col in output_list[0].keys(), 
                ('mutual positive column %r is missing in output cols: %r' 
                 % (col, output_list[0].keys())))
        # if a col is positive, check that the mutual pos cols have data as well
        for row in output_list:
            if row['is_positive']:
                for col in mutual_positive_cols_screen_1:
                    base_colname = col.split('_')[2]
                    this_screen_colname = 'dc_2_%s' % base_colname
                    self.assertTrue(this_screen_colname in row, 
                        'could not find %r in row: %r' % (this_screen_colname, row))
                    if row[this_screen_colname] is not None:
                        self.assertTrue(
                            row[col] is not None,
                            ('mutual positive column %r is missing data in row: %r' 
                             % (col, row)))
            
        # TODO: test mutual column filtering 
        
    def test4_result_value_errors_from_file(self):
        
        logger.info('test4_result_value_errors_from_file...')
        logger.info('create library...')
        library1 = self.create_library({
            'start_plate': 1, 
            'end_plate': 20,
            'screen_type': 'small_molecule' })

        logger.info('create and load well data...')
        input_data = []
        plate = 1
        for i in range(0,384):
            if i in [0,3,6]:
                # setup control wells
                input_data.append(self.create_small_molecule_test_well(
                    plate,i,library_well_type='empty'))
            else:
                input_data.append(self.create_small_molecule_test_well(
                    plate,i,library_well_type='experimental'))
        resource_name = 'well'
        resource_uri = '/'.join([
            BASE_URI_DB,'library', library1['short_name'],resource_name])
        logger.info('put library...')
        resp = self.api_client.put(
            resource_uri, format='sdf', data={ 'objects': input_data } , 
            authentication=self.get_credentials(), 
            **{ 'limit': 0, 'includes': '*'} )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))

        
        logger.info('create screen...')        
        screen = self.create_screen({ 'screen_type': 'small_molecule' })
        
        data_for_get = { 'limit': 0, 'includes': ['*'] }
        data_for_get['CONTENT_TYPE'] = XLSX_MIMETYPE
        data_for_get['HTTP_ACCEPT'] = JSON_MIMETYPE
        data_for_get['HTTP_AUTHORIZATION'] = self.get_credentials()
        
        file = 'ScreenResultRVErrorsTest.xlsx'
        file_out = 'ScreenResultRVErrorsTest_out.xlsx'
        filename = '%s/db/static/test_data/screens/%s' %(APP_ROOT_DIR,file)
        filename_out = (
            '%s/db/static/test_data/screens/%s' %(APP_ROOT_DIR,file_out) )
        logger.info('check file: %r ', os.path.exists(filename))
        with open(filename, 'rb') as input_file:

            # NOTE: now posting as xls, so that the parse errors will be 
            # processed along with the validation errors
            
            resource_name = 'screenresult'
            screen_facility_id = screen['facility_id']
            resource_uri = '/'.join([
                BASE_URI_DB,resource_name,screen_facility_id])
            logger.info('PUT screen result to the server... %r', data_for_get)
            django_test_client = self.api_client.client
            resp = django_test_client.post(
                resource_uri, content_type='application/xls', 
                data=input_file.read(), **data_for_get)
            self.assertTrue(
                resp.status_code == 400, resp.status_code)
            content = self.deserialize(resp)
            logger.info('content; %r', content)
            
            # TODO: check for expected errors
            
            key = 'PL_00001'
            self.assertTrue(find_in_dict(key, content), 
                'key: %r should be in errors: %r' % (key, content))
            sheet_errors = find_in_dict(key, content)
            
            key = '00001:A01-G'
            self.assertTrue(key in sheet_errors, 
                'key: %r should be in errors: %r' % (key, sheet_errors))
            self.assertTrue(
                'could not convert' in str(sheet_errors[key]),
                'error should be a conversion error: %r, %r' 
                    % (key,sheet_errors[key]) )
            key = '00001:A03'
            self.assertTrue(key in sheet_errors, 
                'key: %r should be in errors: %r' % (key, sheet_errors))
            self.assertTrue(
                'unknown exclude' in str(sheet_errors[key]),
                'error should be a excluded cols error: %r, %r' 
                    % (key,sheet_errors[key]) )
            key = '00001:A05-E'
            self.assertTrue(key in sheet_errors, 
                'key: %r should be in errors: %r' % (key, sheet_errors))
            self.assertTrue(
                'could not convert' in str(sheet_errors[key]),
                'error should be a conversion error: %r, %r' 
                    % (key,sheet_errors[key]) )

    def test5_data_column_errors(self):
        
        logger.info('test5_data_column_errors...')
        logger.info('create library...')
        library1 = self.create_library({
            'start_plate': 1, 
            'end_plate': 20,
            'screen_type': 'small_molecule' })

        logger.info('create screen...')        
        screen = self.create_screen({ 'screen_type': 'small_molecule' })
        
        fields = {
            'E': {
                'ordinal': 0,
                'name': 'Field1',
                'data_worksheet_column': 'E',
                'data_type': 'text', 
                'description': 'field 1 description',
                'replicate_ordinal': 1,
            },
            'F': {
                'ordinal': 1,
                'name': 'Field2',
                'data_worksheet_column': 'F',
                #'data_type': 'numeric',
                'decimal_places': 2, 
                'description': 'field 2 description',
                'replicate_ordinal': 1,
                'is_follow_up_data': True,
                'assay_readout_type': 'luminescence',
            },
            'G': {
                'ordinal': 2,
                'name': 'Field3',
                'data_worksheet_column': 'G',
                'data_type': 'numeric',
                'decimal_places': 'x', 
                'description': 'field 3 description',
                'replicate_ordinal': 2,
                'is_follow_up_data': True,
                'assay_readout_type': 'flourescence_intensity',
            },
            'H': {
                'ordinal': 3,
                'name': 'Field4',
                'data_worksheet_column': 'H',
                'data_type': 'confirmed_positive_indicator',
                'description': 'field 4 description',
                'how_derived': 'z-score > 1 for Field3',
                'derived_from_columns': 'F,Y'
            },
            'I': {
                'ordinal': 4,
                'name': 'Field5',
                'data_worksheet_column': 'I',
                'data_type': 'partition_positive_indicator',
                'description': 'field 5 description',
                'how_derived': 'ranking of z-score <.5, ,.5<=x<=1, >1'
            },
        }
        result_values = [
            { 
                'well_id': '00001:A01', 
                'E': 'test value 1',
                'F': 91.19,
                'G': .0011,
                'H': None,  # should be interpreted as 'NT'
                'I': None, # should be interpreted as 'NP'
            },
        ]
        input_data = OrderedDict((
            ('meta', {'screen_facility_id': screen['facility_id'] } ),
            ('fields', fields),
            ('objects', result_values),
        ))

        
        # NOTE: now posting directly, w/out the tp client,
        # so that the parse errors will be 
        # processed along with the validation errors
        # Also, HTTP_ACCEPT is set to JSON, simulating the UI
        data_for_get = { 'limit': 0, 'includes': ['*'] }
        data_for_get['CONTENT_TYPE'] = JSON_MIMETYPE
        data_for_get['HTTP_ACCEPT'] = JSON_MIMETYPE
        data_for_get['HTTP_AUTHORIZATION'] = self.get_credentials()
        
        resource_name = 'screenresult'
        screen_facility_id = screen['facility_id']
        resource_uri = '/'.join([
            BASE_URI_DB,resource_name,screen_facility_id])
        logger.info('PUT screen result to the server...')
        django_test_client = self.api_client.client
        resp = django_test_client.post(
            resource_uri, content_type='application/json', 
            data=json.dumps(input_data), **data_for_get)
        self.assertTrue(
            resp.status_code == 400, resp.status_code)
        content = self.deserialize(resp)
        logger.info('content; %r', content)
        
        key = 'F' # missing data_type
        self.assertTrue(find_in_dict(key, content), 
            'key: %r should be in errors: %r' % (key, content))
        col_errors = find_in_dict(key, content)
        self.assertTrue('data_type' in str(col_errors),
            'error should be a "data_type" error: %r, %r' %(key,col_errors) )
        
        key = 'G' # decimal_places not number
        self.assertTrue(find_in_dict(key, content), 
            'key: %r should be in errors: %r' % (key, content))
        col_errors = find_in_dict(key, content)
        self.assertTrue('decimal_places' in str(col_errors),
            'error should be a "decimal_places" error: %r, %r' %(key,col_errors) )
        
        key = 'H' # derived from col dne
        self.assertTrue(find_in_dict(key, content), 
            'key: %r should be in errors: %r' % (key, content))
        col_errors = find_in_dict(key, content)
        self.assertTrue(
            'derived_from_columns' in str(col_errors),
            'error should be a "derived_from_columns" error: %r, %r' 
                % (key,col_errors) )
        

class ScreenResource(DBResourceTestCase):
        
    def setUp(self):
        super(ScreenResource, self).setUp()

    def tearDown(self):
        DBResourceTestCase.tearDown(self)
        Screen.objects.all().delete()
        Library.objects.all().delete()
        LibraryScreening.objects.all().delete()
        ApiLog.objects.all().delete()
        ScreensaverUser.objects.all().exclude(username='testsuper').delete()
        
    def test1_create_screen(self):

        logger.info('test1_create_screen...')        
        data = {
            'screen_type': 'small_molecule',
            'cell_lines': ['293_hek_293','colo_858'],
        }
        screen_item = self.create_screen(data=data)
        
        self.assertTrue(
            'facility_id' in screen_item, 
            'the facility_id was not created')
        
        for key, value in data.items():
            self.assertEqual(value, screen_item[key], 
                'key %r, val: %r not expected: %r' % (key, value, screen_item[key]))
        logger.debug('screen created: %r', screen_item)

    def test2_create_library_screening(self):

        logger.info('test2_create_library_screening...')
                
        # Set up dependencies
        logger.info('create users...')
        self.screening_user = self.create_screensaveruser({ 
            'username': 'screening1'
        })
        logger.info('create library...')
        library1 = self.create_library({
            'start_plate': 1000, 
            'end_plate': 1005,
            'screen_type': 'small_molecule' })

        library2 = self.create_library({
            'start_plate': 2000, 
            'end_plate': 2040,
            'screen_type': 'small_molecule' })

        logger.info('set some experimental wells...')
        plate = 1000
        experimental_well_count = 20
        input_well_data = [
            self.create_small_molecule_test_well(
                plate,i,library_well_type='experimental',
                molar_concentration='0.001') 
            for i in range(0,experimental_well_count)]
        resource_name = 'well'
        resource_uri = '/'.join([
            BASE_URI_DB,'library', library1['short_name'],resource_name])
        resp = self.api_client.put(
            resource_uri, format='sdf', data={ 'objects': input_well_data } , 
            authentication=self.get_credentials(), 
            **{ 'limit': 0, 'includes': '*'} )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))

        logger.info('create library copy...')
        library_copy1_input = {
            'library_short_name': library1['short_name'],
            'copy_name': "A",
            'usage_type': "library_screening_plates",
            'initial_plate_well_volume': '0.000010',
            'initial_plate_status': 'available'
        }  
        resource_uri = BASE_URI_DB + '/librarycopy'
        resource_test_uri = '/'.join([
            resource_uri,library_copy1_input['library_short_name'],library_copy1_input['copy_name']])
        library_copy1 = self._create_resource(
            library_copy1_input, resource_uri, resource_test_uri, 
            excludes=['initial_plate_well_volume','initial_plate_status'])
        logger.info('created: %r', library_copy1)
 
        library_copy2_input = library_copy1_input.copy()
        library_copy2_input['library_short_name'] = library2['short_name']
        resource_test_uri = '/'.join([
            resource_uri,library_copy2_input['library_short_name'],library_copy2_input['copy_name']])
        library_copy2 = self._create_resource(
            library_copy2_input, resource_uri, resource_test_uri,
            excludes=['initial_plate_well_volume','initial_plate_status'])
        logger.info('created: %r', library_copy2)
        
        logger.info('create screen...')        
        screen = self.create_screen({
            'screen_type': 'small_molecule'
            })

        lps_format = '{library_short_name}:{copy_name}:{{start_plate}}-{{end_plate}}'
        plate_range1 = lps_format.format(**library_copy1).format(**library1)
        plate_range2 = lps_format.format(**library_copy2).format(
                start_plate=library2['start_plate'],
                end_plate=int(library2['start_plate']+10))
        library_plates_screened = [ plate_range1, plate_range2 ]
        
        library_screening_input = {
            'screen_facility_id': screen['facility_id'],
            'date_of_activity': "2008-01-18",
            'assay_protocol':'test assay protocol',
            'assay_protocol_type': '',
            'is_for_external_library_plates': False,
            'library_plates_screened': library_plates_screened,
            'number_of_replicates': 2,
            'performed_by_username': self.admin_user['username'],
            'volume_transferred_per_well_from_library_plates': "0.000006000",
            'volume_transferred_per_well_to_assay_plates': "0.000002000"
        }
        resource_uri = BASE_URI_DB + '/libraryscreening'
        resource_test_uri = BASE_URI_DB + '/libraryscreening'
        data_for_get = {
            'screen_facility_id__eq': screen['facility_id']
        }
        
        # First try creating (failed) library_screening with invalid inputs
        # 1. test validations
        key = 'screen_facility_id' 
        msg = 'input missing a %r should fail' % key
        logger.info('test %r', msg)
        invalid_input1 = library_screening_input.copy()
        del invalid_input1['screen_facility_id'] 
        errors, resp = self._create_resource(
            invalid_input1, resource_uri, resource_test_uri, expect_fail=True)
        self.assertTrue(resp.status_code==400, msg)
        self.assertTrue(
            find_in_dict(key, errors), 
            'test: %s, not in errors: %r' %(msg,errors))
        
        key = 'library_plates_screened' 
        # 2. create a library_plate_range with invalid library name:
        # even though the plate range is correct, this should fail,
        # and the transaction should cancel the creation of the libraryscreening
        value = [ lps_format.format(
            library_short_name='**',copy_name='A').format(**library1)]
        msg = 'invalid format for %r should fail' % key
        logger.info('test %r', msg)
        invalid_input2 = library_screening_input.copy()
        invalid_input2[key] =  value
        errors, resp = self._create_resource(
            invalid_input2, resource_uri, resource_test_uri, expect_fail=True)
        self.assertTrue(resp.status_code==400, msg)
        self.assertTrue(find_in_dict(key, errors), 
            'Error: response error not found: %r, obj: %r' %(key, errors))

        # 3. Invalid plate range
        key = 'library_plates_screened' 
        value = [ lps_format.format(**library_copy1).format(**{
            'start_plate': library1['start_plate'],
            'end_plate': int(library1['start_plate'])-1 }) ]
        msg = 'invalid plate range in %r for  %r should fail' % (value,key)
        invalid_input3 = library_screening_input.copy()
        invalid_input3[key] =  value
        errors, resp = self._create_resource(
            invalid_input3, resource_uri, resource_test_uri, expect_fail=True)
        self.assertTrue(resp.status_code==400, msg)
        self.assertTrue(find_in_dict(key, errors), 
            'test: %s, not in errors: %r' %(key,errors))

        # 4. Overlapping plate range
        key = 'library_plates_screened' 
        value = [ 
            lps_format.format(**library_copy1).format(**{
                'start_plate': library2['start_plate'],
                'end_plate': int(library2['start_plate'])+2 }),
            lps_format.format(**library_copy1).format(**{
                'start_plate': library2['start_plate']+1,
                'end_plate': int(library2['start_plate'])+4 }),
        ]
        msg = 'overlapping plate ranges in %r for  %r should fail' % (value,key)
        logger.info('test %r', msg)
        invalid_input4 = library_screening_input.copy()
        invalid_input4[key] =  value
        errors, resp = self._create_resource(
            invalid_input4, resource_uri, resource_test_uri, expect_fail=True)
        self.assertTrue(resp.status_code==400, msg)
        self.assertTrue(find_in_dict(key, errors), 
            'test: %s, not in errors: %r' %(key,errors))

        # 5. Finally, create valid input
        logger.info('Patch batch_edit: cred: %r', self.username)
        resp = self.api_client.post(
            resource_uri,format='json', 
            data=library_screening_input, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        
        # 5.a Inspect meta "Result" section
        resp = self.deserialize(resp)
        logger.info('resp: %r', resp)

        self.assertTrue('meta' in resp, '%r' % resp)
        self.assertTrue(API_MSG_RESULT in resp['meta'], '%r' % resp)
        
        actual_result_meta = resp['meta'][API_MSG_RESULT]
        expected_result_meta = {
            API_MSG_SCREENING_PLATES_UPDATED: 17,
            API_MSG_SCREENING_ADDED_PLATE_COUNT: 17, 
            API_MSG_SCREENING_DELETED_PLATE_COUNT: 0,
            API_MSG_SCREENING_EXTANT_PLATE_COUNT: 0,
            API_MSG_SCREENING_TOTAL_PLATE_COUNT: 17
        }
        for k,v in expected_result_meta.items():
            self.assertEqual(v,actual_result_meta[k], 
                'k: %r, expected: %r, actual: %r' % (k, v, actual_result_meta))
        
        self.assertTrue(len(resp['objects']) == 1)
        library_screening_output = resp['objects'][0]
        
        # 5.b Inspect the returned library screening
        logger.info('TODO: validate the library screening returned: %r', library_screening_output)
        
        for k,v in library_screening_input.items():
            self.assertEqual(v, library_screening_output[k])

        # 5.c verify new plate volumes (just the first library)
        # retrieve plates from the server
        _data_for_get = { 
            'plate_number__range': [library1['start_plate'],library1['end_plate']],
            'copy_name__eq': library_copy1['copy_name']
            }
        plate_resource_uri = BASE_URI_DB + '/librarycopyplate'
        resp = self.api_client.get(
            plate_resource_uri, format='json', 
            authentication=self.get_credentials(), data=_data_for_get)
        new_obj = self.deserialize(resp)
        expected_plates = library1['end_plate']-library1['start_plate']+1
        self.assertTrue(len(new_obj['objects']),expected_plates)
        
        expected_remaining_volume = (
            Decimal(library_copy1_input['initial_plate_well_volume']) - 
            Decimal(library_screening_input['volume_transferred_per_well_from_library_plates']))
        for plate_data in new_obj['objects']:
            logger.info('inspect plate: %r', plate_data)
            self.assertTrue(
                Decimal(plate_data['remaining_well_volume'])==expected_remaining_volume,
                'expected: %r, actual: %r' % (
                    expected_remaining_volume, plate_data))

        # 5.d. Test Screen statistics
        screen = self.get_screen(screen['facility_id'])

        key = 'library_screenings'
        expected_value = 1
        self.assertTrue(screen[key] == expected_value,
            (key,'expected_value',expected_value,
                'returned value',screen[key]))

        key = 'screened_experimental_well_count'
        expected_value = experimental_well_count
        self.assertTrue(screen[key] == expected_value,
            (key,'expected_value',expected_value,
                'returned value',screen[key]))
        key = 'unique_screened_experimental_well_count'
        expected_value = experimental_well_count
        self.assertTrue(screen[key] == expected_value,
            (key,'expected_value',expected_value,
                'returned value',screen[key]))
        key = 'library_plates_screened'
        expected_value = 17 # 6 in library1, 11 in library2
        self.assertTrue(screen[key] == expected_value,
            (key,'expected_value',expected_value,
                'returned value',screen[key]))
        key = 'assay_plates_screened'
        expected_value = 34 # lps * 2 replicates
        self.assertTrue(screen[key] == expected_value,
            (key,'expected_value',expected_value,
                'returned value',screen[key]))

        # 5.e Inspect PATCH logs after adding a plate range

        data_for_get={ 
            'limit': 0, 
            'includes': ['*'],
            'ref_resource_name': 'libraryscreening', 
            'key': library_screening_output['activity_id'],
            'api_action': API_ACTION_CREATE
        }
        apilogs = self.get_list_resource(
            BASE_REPORTS_URI + '/apilog', 
            data_for_get=data_for_get )
        logger.info('logs: %r', apilogs)
        self.assertTrue(
            len(apilogs) == 1, 'too many apilogs found: %r' % apilogs)
        apilog = apilogs[0]
        logger.info('apilog: %r', apilog)
        self.assertTrue(apilog['diff_keys'] is not None, 'no diff_keys' )
        self.assertTrue('library_plates_screened' in apilog['diff_keys'])
        self.assertTrue(plate_range1 in apilog['diffs'], 
            'plate_range1: %r not in diffs: %r'
            % (plate_range1, apilog['diffs']))
        
        self.assertTrue(apilog['child_logs'] == 17, 
            'wrong child_logs count: %r' % apilog)
        data_for_get={ 
            'limit': 0, 
            'includes': ['*'],
            'parent_log_id': apilog['id']
        }
        apilogs = self.get_list_resource(
            BASE_REPORTS_URI + '/apilog', 
            data_for_get=data_for_get )
        self.assertTrue(
            len(apilogs) == 17, 
            'wrong number of child logs returned: %d: %r' 
            % (len(apilogs), apilogs))
        apilog = apilogs[0]
        logger.info('apilog: %r', apilog)
        self.assertTrue(apilog['diff_keys'] is not None, 'no diff_keys' )
        expected_remaining_volume = (
            Decimal(library_copy1_input['initial_plate_well_volume']) - 
            Decimal(library_screening_output['volume_transferred_per_well_from_library_plates']))
        self.assertTrue('remaining_well_volume' in apilog['diff_keys'])
        diffs = json.loads(apilog['diffs'])
        logger.info('diffs: %r', diffs)
        self.assertEqual(
            expected_remaining_volume, Decimal(diffs['remaining_well_volume'][1]))
        


        # 7. Test - add a plate_range
        library_screening_input2 = { 
            'activity_id': library_screening_output['activity_id'],
            'library_plates_screened': 
                library_screening_output['library_plates_screened'] 
            }
        # add 6 more plates
        added_plate_range_start = library2['start_plate']+15
        added_plate_range_end = library2['start_plate']+20
        added_plate_range = lps_format.format(**library_copy2).format(
                start_plate=added_plate_range_start,
                end_plate=added_plate_range_end )
        library_screening_input2['library_plates_screened'].append(
            added_plate_range)
        logger.info('input: %r', library_screening_input2)
        resp = self.api_client.patch(
            resource_uri, 
            format='json', data=library_screening_input2, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))

        # 7.a Inspect meta "Result" section
        resp = self.deserialize(resp)
        logger.info('resp: %r', resp)

        self.assertTrue('meta' in resp, '%r' % resp)
        self.assertTrue(API_MSG_RESULT in resp['meta'], '%r' % resp)
        actual_result_meta = resp['meta'][API_MSG_RESULT]
        expected_result_meta = {
            API_MSG_SCREENING_PLATES_UPDATED: 6,
            API_MSG_SCREENING_ADDED_PLATE_COUNT: 6, 
            API_MSG_SCREENING_DELETED_PLATE_COUNT: 0,
            API_MSG_SCREENING_EXTANT_PLATE_COUNT: 17,
            API_MSG_SCREENING_TOTAL_PLATE_COUNT: 23,
        }
        for k,v in expected_result_meta.items():
            self.assertEqual(v,actual_result_meta[k], 
                'k: %r, expected: %r, actual: %r' % (k, v, actual_result_meta))
        
        self.assertTrue(len(resp['objects']) == 1)
        library_screening_output2 = resp['objects'][0]
        for k,v in library_screening_input2.items():
            self.assertEqual(v, library_screening_output2[k])
        
        # 7.c Inspect PATCH logs after adding a plate range

        data_for_get={ 
            'limit': 0, 
            'includes': ['*'],
            'ref_resource_name': 'libraryscreening', 
            'key': library_screening_output2['activity_id'],
            'api_action': API_ACTION_PATCH
        }
        apilogs = self.get_list_resource(
            BASE_REPORTS_URI + '/apilog', 
            data_for_get=data_for_get )
        logger.info('logs: %r', apilogs)
        self.assertTrue(
            len(apilogs) == 1, 'too many apilogs found: %r' % apilogs)
        apilog = apilogs[0]
        logger.info('apilog: %r', apilog)
        self.assertTrue(apilog['diff_keys'] is not None, 'no diff_keys' )
        self.assertTrue('library_plates_screened' in apilog['diff_keys'])
        self.assertTrue(added_plate_range in apilog['diffs'], 
            'added_plate_range: %r not in diffs: %r'
            % (added_plate_range, apilog['diffs']))
        
        self.assertTrue(apilog['child_logs'] == 6, 
            'wrong child_logs count: %r' % apilog)
        data_for_get={ 
            'limit': 0, 
            'includes': ['*'],
            'parent_log_id': apilog['id']
        }
        apilogs = self.get_list_resource(
            BASE_REPORTS_URI + '/apilog', 
            data_for_get=data_for_get )
        self.assertTrue(
            len(apilogs) == 6, 
            'wrong number of child logs returned: %d: %r' 
            % (len(apilogs), apilogs))
        apilog = apilogs[0]
        logger.info('apilog: %r', apilog)
        self.assertTrue(apilog['diff_keys'] is not None, 'no diff_keys' )
        expected_remaining_volume = (
            Decimal(library_copy1_input['initial_plate_well_volume']) - 
            Decimal(library_screening_output2['volume_transferred_per_well_from_library_plates']))
        self.assertTrue('remaining_well_volume' in apilog['diff_keys'])
        diffs = json.loads(apilog['diffs'])
        logger.info('diffs: %r', diffs)
        self.assertEqual(
            expected_remaining_volume, Decimal(diffs['remaining_well_volume'][1]))
        
        # 7.e check a modified plate
        plate_resource_uri = BASE_URI_DB + '/librarycopyplate'
        modified_plate = self.get_single_resource(plate_resource_uri, {
            'copy_name': library_copy2['copy_name'],
            'plate_number': added_plate_range_start })
        logger.info('modified plate: %r', modified_plate)
        self.assertTrue(modified_plate is not None)
        self.assertEqual(
            expected_remaining_volume, 
            Decimal(modified_plate['remaining_well_volume']))
        
        # 8. Test - delete the first plate_range (6 plates)
        
        plate_range_to_delete = library_screening_output2['library_plates_screened'][0]
        library_screening_input3 = { 
            'activity_id': library_screening_output2['activity_id'],
            'library_plates_screened': 
                library_screening_output2['library_plates_screened'][1:] 
            }
        logger.info('input: %r', library_screening_input3)
        resp = self.api_client.patch(
            resource_uri, 
            format='json', data=library_screening_input3, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        
        # 8.a Inspect meta "Result" section
        resp = self.deserialize(resp)
        logger.info('resp: %r', resp)

        self.assertTrue('meta' in resp, '%r' % resp)
        self.assertTrue(API_MSG_RESULT in resp['meta'], '%r' % resp)
        actual_result_meta = resp['meta'][API_MSG_RESULT]
        expected_result_meta = {
            API_MSG_SCREENING_PLATES_UPDATED: 6,
            API_MSG_SCREENING_ADDED_PLATE_COUNT: 0, 
            API_MSG_SCREENING_DELETED_PLATE_COUNT: 6,
            API_MSG_SCREENING_EXTANT_PLATE_COUNT: 17,
            API_MSG_SCREENING_TOTAL_PLATE_COUNT: 17
        }
        for k,v in expected_result_meta.items():
            self.assertEqual(v,actual_result_meta[k], 
                'k: %r, expected: %r, actual: %r' % (k, v, actual_result_meta))
        
        self.assertTrue(len(resp['objects']) == 1)
        library_screening_output3 = resp['objects'][0]
        for k,v in library_screening_input3.items():
            self.assertEqual(v, library_screening_output3[k])

        # verify new plate volumes = initial_plate_well_volume
        # retrieve plates from the server
        _data_for_get = { 
            'plate_number__range': [library1['start_plate'],library1['end_plate']],
            'copy_name__eq': library_copy1['copy_name']
            }
        plate_resource_uri = BASE_URI_DB + '/librarycopyplate'
        resp = self.api_client.get(
            plate_resource_uri, format='json', 
            authentication=self.get_credentials(), data=_data_for_get)
        new_obj = self.deserialize(resp)
        expected_plates = library1['end_plate']-library1['start_plate']+1
        self.assertTrue(len(new_obj['objects']),expected_plates)
        
        expected_remaining_volume = Decimal(library_copy1_input['initial_plate_well_volume'])
        for plate_data in new_obj['objects']:
            self.assertTrue(
                Decimal(plate_data['remaining_well_volume'])==expected_remaining_volume,
                'expected: %r, actual: %r' % (
                    expected_remaining_volume, plate_data))
            self.assertTrue(
                Decimal(plate_data['well_volume'])==expected_remaining_volume,
                'initial well volume expected: %r, actual: %r' % (
                    expected_remaining_volume, plate_data))
        
        
        # 9. test valid input with start_plate==end_plate (no end plate)
        
        logger.info('test valid single plate input...')

        single_plate_lps_format = '{library_short_name}:{copy_name}:{{start_plate}}'
        single_plate_lps_return_format = \
            '{library_short_name}:{copy_name}:{{start_plate}}-{{start_plate}}'
        library_plates_screened = [
            single_plate_lps_format.format(**library_copy1).format(**library1),
            single_plate_lps_format.format(**library_copy2).format(
                start_plate=library2['start_plate'])
        ]
        library_plates_screened_return_formatted = [
            single_plate_lps_return_format.format(**library_copy1).format(**library1),
            single_plate_lps_return_format.format(**library_copy2).format(
                start_plate=library2['start_plate'])
        ]

        library_screening_input4 = library_screening_input.copy()
        library_screening_input4['date_of_activity'] = '2016-08-01'
        library_screening_input4['library_plates_screened'] =  library_plates_screened
        resp = self.api_client.post(
            resource_uri,format='json', 
            data=library_screening_input4, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))

        # 9.a Inspect meta "Result" section
        resp = self.deserialize(resp)

        self.assertTrue('meta' in resp, '%r' % resp)
        self.assertTrue(API_MSG_RESULT in resp['meta'], '%r' % resp)
        actual_result_meta = resp['meta'][API_MSG_RESULT]
        expected_result_meta = {
            API_MSG_SCREENING_PLATES_UPDATED: 2,
            API_MSG_SCREENING_ADDED_PLATE_COUNT: 2, 
            API_MSG_SCREENING_DELETED_PLATE_COUNT: 0,
            API_MSG_SCREENING_EXTANT_PLATE_COUNT: 0,
            API_MSG_SCREENING_TOTAL_PLATE_COUNT: 2
        }
        for k,v in expected_result_meta.items():
            self.assertEqual(v,actual_result_meta[k], 
                'k: %r, expected: %r, actual: %r' % (k, v, actual_result_meta))

        self.assertTrue(len(resp['objects']) == 1)
        library_screening_output4 = resp['objects'][0]
        for k,v in library_screening_input4.items():
            if k == 'library_plates_screened':
                continue # see next test, below
            self.assertEqual(v, library_screening_output4[k])
        logger.info('created library screening, with single-plate range: %r',
            library_screening_output4)
        found_count = 0
        for lps in library_screening_output4['library_plates_screened']:
            for lps_expected in library_plates_screened_return_formatted:
                if lps_expected==lps:
                    found_count += 1
        self.assertTrue(found_count==2, 
            'single plate test, returned ranges, expected: %r, returned: %r'
            %(library_plates_screened_return_formatted,
                library_screening_output4['library_plates_screened']))
        
        # Test Screen statistics

        screen = self.get_screen(screen['facility_id'])

        key = 'library_screenings'
        expected_value = 2
        self.assertTrue(screen[key] == expected_value,
            (key,'expected_value',expected_value,
                'returned value',screen[key]))
        
        # 10. test deletion of a library screening
        
    def test3_create_publication(self):

        logger.info('test3_create_publication ...')
        
        self.screening_user = self.create_screensaveruser({ 
            'username': 'screening1'
        })
        logger.info('create screen...')        
        screen = self.create_screen({
            'screen_type': 'small_molecule'
            })
        
        publication_data = {
            'pubmed_id': 'PM00001',
            'pubmed_central_id': 12121212,
            'authors': "Smith JA, White EA, Sowa ME, Powell ML, Ottinger M, Harper JW, Howley PM",
            'journal': "Proceedings of the National Academy of Sciences of the United States of America",
            'pages': "3752-7",
            'screen_facility_id': screen['facility_id'],
            'title': "Genome-wide siRNA screen identifies SMCX, EP400, and Brd4 as E2-dependent regulators of human papillomavirus oncogene expression.",
            'volume': "107",
            'year_published': "2010"            
        }
        resource_uri = '/'.join([
            BASE_URI_DB, 'screen',screen['facility_id'],'publications'])
        # NOTE: post data because publications may include attached files
        content_type = MULTIPART_CONTENT
        resource_uri = '/'.join([
            BASE_URI_DB, 'screen',screen['facility_id'],'publications'])
        authentication=self.get_credentials()
        kwargs = {}
        kwargs['HTTP_AUTHORIZATION'] = authentication
        kwargs['HTTP_ACCEPT'] = JSON_MIMETYPE

        # Add an attached file and post the publication
        base_filename = 'iccbl_sm_user_agreement_march2015.pdf'
        filename = '%s/db/static/test_data/useragreement/%s' %(APP_ROOT_DIR,base_filename)
        with open(filename) as input_file:

            logger.info('POST publication with attached_file to the server...')
            publication_data['attached_file'] = input_file
            django_test_client = self.api_client.client
            resp = django_test_client.post(
                resource_uri, content_type=content_type, 
                data=publication_data, **kwargs)
            self.assertTrue(
                resp.status_code == 201, 
                (resp.status_code,self.get_content(resp)))
        
        resp = self.api_client.get(
            resource_uri,
            authentication=self.get_credentials(),
            data={ 'limit': 0, 'includes': '*'} )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        logger.info('new obj: %s ' % new_obj)
        self.assertTrue(
            len(new_obj['objects'])==1,'wrong number of publications returned')
    
        publication_received = new_obj['objects'][0]
        result,msgs = assert_obj1_to_obj2(
            publication_data, publication_received, 
            excludes=['attached_file'])
        self.assertTrue(result,msgs)
    
        # Check for the attached file
        uri = '/db/publication/%s/attached_file' % publication_received['publication_id']
        try:
            admin_user = User.objects.get(username=self.admin_user['username'])
            view, args, kwargs = resolve(uri)
            kwargs['request'] = self.api_client.client.request()
            kwargs['request'].user=admin_user
            result = view(*args, **kwargs)
            self.assertTrue(
                result.status_code == 200, 
                'No attached file found, status_code: %r, %r' 
                    % (result.status_code, result))
            output_filename = '%s.out.%s' % tuple(filename.split('.'))
            logger.info('write %s to %r', filename, output_filename)
            with open(output_filename, 'w') as out_file:
                out_file.write(self.get_content(result))
            self.assertTrue(filecmp.cmp(filename,output_filename), 
                'input file: %r, not equal to output file: %r' 
                % (filename, output_filename))
            os.remove(output_filename)    
        except Exception, e:
            logger.info('no file found at: %r', uri)
            raise
        
        # Test apilog
        resource_uri = BASE_REPORTS_URI + '/apilog'
        data_for_get={ 
            'limit': 0, 
            'ref_resource_name': 'publication', 
        }
        apilogs = self.get_list_resource(resource_uri, data_for_get=data_for_get )
        self.assertTrue(len(apilogs) == 1, 'too many apilogs found: %r' % apilogs)
        apilog = apilogs[0]
        logger.debug('publication log: %r', apilog)
        self.assertTrue(apilog['api_action'] == 'CREATE')
        self.assertTrue('attached_filename' in apilog['diff_keys'])
        self.assertTrue(base_filename in apilog['diffs'])
        
        return publication_received
        
    def test4_delete_publication(self):
        
        logger.info('test4_delete_publication...')
        
        publication_received = self.test3_create_publication()
        resource_uri = '/'.join([
            BASE_URI_DB, 'screen',publication_received['screen_facility_id'],
            'publications', str(publication_received['publication_id'])])
        
        logger.info('submit the publication for deletion...')
        resp = self.api_client.delete(
            resource_uri, authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code == 204, 
            (resp.status_code, self.get_content(resp)))
        resp = self.api_client.get(
            resource_uri,
            authentication=self.get_credentials(),
            data={ 'limit': 0, 'includes': '*'} )
        self.assertTrue(
            resp.status_code == 404, 
            ('error, publication should be deleted', resp.status_code, 
                self.get_content(resp)))

        # Check for the attached file record
        resource_uri = '/'.join([
            BASE_URI_DB, 'attached_file', 
            str(publication_received['attached_file_id'])])
        resp = self.api_client.get(
            resource_uri,
            authentication=self.get_credentials(),
            data={ 'limit': 0, 'includes': '*'} )
        self.assertTrue(
            resp.status_code == 404, 
            (resp.status_code, self.get_content(resp)))
        
        # Check for the attached file
        uri = '/db/publication/%s/attached_file' % publication_received['publication_id']
        try:
            admin_user = User.objects.get(username=self.admin_user['username'])
            view, args, kwargs = resolve(uri)
            kwargs['request'] = self.api_client.client.request()
            kwargs['request'].user=admin_user
            result = view(*args, **kwargs)
            self.assertTrue(
                result.status_code == 404, 
                'Attached file found after delete, status_code: %r, %r' 
                    % (result.status_code, result))
        except Exception, e:
            logger.exception('exception when trying to locate: %r', uri)
            raise
    
    def test5_pin_transfer_approval(self):
        
        logger.info('test5_pin_transfer_approval...')
        # Create a Screen
        data = {
            'screen_type': 'small_molecule',
        }
        screen_item = self.create_screen(data=data)
        
        self.assertTrue(
            'facility_id' in screen_item, 
            'the facility_id was not created')
        
        for key, value in data.items():
            self.assertEqual(value, screen_item[key], 
                'key %r, val: %r not expected: %r' 
                % (key, value, screen_item[key]))

        logger.info('screen created: %r', screen_item)

        self.screening_user = self.create_screensaveruser({ 
            'username': 'screening1'
        })

        # 1. Set the pin_transfer data
        pin_transfer_data_expected = {
            'pin_transfer_approved_by_username': self.screening_user['username'],
            'pin_transfer_date_approved': _now().date().strftime("%Y-%m-%d"),
            'pin_transfer_comments': 'test pin_transfer_comments' }
        
        screen_update_data = {
            'facility_id': screen_item['facility_id']
            }
        
        for key, val in pin_transfer_data_expected.items():
            screen_update_data[key] = val
        resource_uri = BASE_URI_DB + '/screen/' + screen_update_data['facility_id']
        resp = self.api_client.patch(
            resource_uri, 
            format='json', data=screen_update_data, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        
        logger.info('get the updated pin_transfer screen data')
        new_screen_item = self.get_single_resource(resource_uri)
        logger.info('retrieved: %r', new_screen_item)
        for key,val in pin_transfer_data_expected.items():
            self.assertEqual(new_screen_item[key],pin_transfer_data_expected[key],
                'key: %r, %r, %r' % (key,new_screen_item[key],pin_transfer_data_expected[key]))
            
        # 2. Modify the pin_transfer comment
        screen_update_data = {
            'facility_id': screen_item['facility_id']
            }
        screen_update_data['pin_transfer_comments'] = 'New test pin transfer comment'
        resp = self.api_client.patch(
            resource_uri, 
            format='json', data=screen_update_data,
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        
        logger.info('get the updated pin_transfer screen data')
        new_screen_item = self.get_single_resource(resource_uri)
        for key,val in pin_transfer_data_expected.items():
            if key == 'pin_transfer_comments':
                self.assertEqual(new_screen_item[key],screen_update_data[key])
            else:
                self.assertEqual(new_screen_item[key],pin_transfer_data_expected[key],
                    'key: %r, %r, %r' % (key,new_screen_item[key],pin_transfer_data_expected[key]))
        
        
class MutualScreensTest(DBResourceTestCase,ResourceTestCase):
    
    def test_mutual_positives_to_screen(self):
        pass
        # create two screens
        # create two screen results
        # set data sharing levels
        # find assay wells that overlap
        
class ScreensaverUserResource(DBResourceTestCase):
        
    def setUp(self):
        super(ScreensaverUserResource, self).setUp()

    def tearDown(self):
        DBResourceTestCase.tearDown(self)
        
        logger.info('delete resources')
        UserChecklistItem.objects.all().delete()
        AttachedFile.objects.all().delete()
        ServiceActivity.objects.all().delete()
        logger.info('delete users, including: %r', self.username)
        ScreensaverUser.objects.all().exclude(username=self.username).delete()
         
        UserGroup.objects.all().delete()
        UserProfile.objects.all().exclude(username=self.username).delete()
        User.objects.all().exclude(username=self.username).delete()
        ApiLog.objects.all().delete()

    def test01_create_user_iccbl(self):

        logger.info('test01_create_user_iccbl...')
        # create users using only ecommons, username
        simple_user_input = { 
            'ecommons_id': 'tester01c',
            'first_name': 'FirstName01c',
            'last_name': 'LastName01c',    
            'email': 'tester01c@limstest.com',    
            'harvard_id': '332122',
            'harvard_id_expiration_date': '2018-05-01',
        }
        resource_uri = BASE_URI_DB + '/screensaveruser'
        resource_test_uri = '/'.join([
            resource_uri,simple_user_input['ecommons_id']])
        created_user = self._create_resource(
            simple_user_input, resource_uri, resource_test_uri)
        self.assertTrue(
            simple_user_input['ecommons_id']==created_user['username'],
            'username should equal the ecommons id if only ecommons is'
            ' provided: %r, %r' % (simple_user_input,created_user))
        
    def test0_create_user(self):
        
        logger.info('test0_create_user...')
        self.user1 = self.create_screensaveruser({ 'username': 'st1'})
        self.screening_user = self.create_screensaveruser(
            { 'username': 'screening1'})
        
        self.test_admin_user = self.create_screensaveruser(
            { 'username': 'adminuser'})

        # create an admin
        
        patch_obj = { 'objects': [
            {
                'username': 'adminuser',
                'is_superuser': True
            }]
        }
        resource_uri = BASE_REPORTS_URI + '/user'

        try:       
            resp = self.api_client.patch(resource_uri, 
                format='json', data=patch_obj, 
                authentication=self.get_credentials())
            self.assertTrue(
                resp.status_code in [200,201,202], 
                (resp.status_code, self.get_content(resp)))
        except Exception, e:
            logger.exception('on patching adminuser %s' % patch_obj)
            raise

    def test1_patch_usergroups(self):
        
        logger.info('test1_patch_usergroups...')
        self.test0_create_user();
        group_patch = { 'objects': [
            { 
                'name': 'usergroup1'
            },
            { 
                'name': 'usergroup2'
            },
            { 
                'name': 'usergroup3'
            }
        ]};
        
        try:       
            resource_uri = BASE_REPORTS_URI + '/usergroup/'
            resp = self.api_client.put(resource_uri, 
                format='json', data=group_patch, 
                authentication=self.get_credentials())
            self.assertTrue(
                resp.status_code in [200,201,202], 
                (resp.status_code, self.get_content(resp)))

            resp = self.api_client.get(resource_uri, format='json', 
                authentication=self.get_credentials(), data={ 'limit': 999 })
            self.assertTrue(
                resp.status_code in [200,201,202], 
                (resp.status_code, self.get_content(resp)))
            new_obj = self.deserialize(resp)
            self.assertEqual(
                len(new_obj['objects']), len(group_patch['objects']), new_obj)
            
            for i,item in enumerate(group_patch['objects']):
                result, obj = find_obj_in_list(item, new_obj['objects'])
                self.assertTrue(
                    result, 
                    ('bootstrap item not found', item, new_obj['objects']))
                logger.info('item found: %r', obj)        
        except Exception, e:
            logger.exception('on group_patch: %r', group_patch)
            raise

        userpatch = { 'objects': [   
            {
                'username': self.user1['username'],
                'usergroups': ['usergroup1']
            },
            {
                'username': self.screening_user['username'],
                'usergroups': ['usergroup2']
            },
            {
                'username': self.test_admin_user['username'],
                'usergroups': ['usergroup3']
            },
        ]};
        resource_uri = BASE_URI_DB + '/screensaveruser'
        resp = self.api_client.patch(resource_uri, 
            format='json', data=userpatch, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200,201,202], 
            (resp.status_code, self.get_content(resp)))

        data_for_get = { 'limit': 0, 'includes': ['*'] }
        resp = self.api_client.get(resource_uri, format='json', 
            authentication=self.get_credentials(), data=data_for_get )
        new_obj = self.deserialize(resp)

        extant_user_count = 4 # 3 created in this test + testsuper (setupModule)
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        self.assertEqual(len(new_obj['objects']), extant_user_count, (new_obj))
        
        for i,item in enumerate(userpatch['objects']):
            result, obj = find_obj_in_list(item, new_obj['objects'])
            self.assertTrue(
                result, 
                ('bootstrap item not found', item, new_obj['objects']))
            logger.debug('item found: %r', obj)        

    def test2_user_checklist_items(self):
        
        logger.info('test2_user_checklist_items...')
        self.test0_create_user();
        
        test_username = self.user1['username']
        checklist_item_patch = {
            'objects': [
                { 
                    'admin_username': self.test_admin_user['username'], 
                    'item_group': "mailing_lists_wikis",
                    'item_name': "added_to_iccb_l_nsrb_email_list",
                    'status': "activated",
                    'status_date': "2015-09-02",
                    'username': test_username
                }
            ]}
        
        try:       
            resource_uri = BASE_URI_DB + '/userchecklistitem/%s' % test_username
            resp = self.api_client.patch(
                resource_uri, 
                format = 'json', 
                data = checklist_item_patch, 
                authentication=self.get_credentials())
            self.assertTrue(
                resp.status_code in [200,201,202], 
                (resp.status_code, self.get_content(resp)))

            data_for_get = { 'limit': 0, 'includes': ['*'] }
            resp = self.api_client.get(
                resource_uri + '/mailing_lists_wikis/added_to_iccb_l_nsrb_email_list',
                format='json', 
                authentication=self.get_credentials(), data=data_for_get )
            self.assertTrue(
                resp.status_code in [200], 
                (resp.status_code, self.get_content(resp)))
            new_obj = self.deserialize(resp)
            result,msgs = assert_obj1_to_obj2(
                checklist_item_patch['objects'][0], new_obj)
            self.assertTrue(result,msgs)
        
        except Exception, e:
            logger.exception('on userchecklist')
            raise e
        
        # TODO: checklistitem logs
                    
    def test3_attached_files(self):
        
        logger.info('test3_attached_files...')
        self.test0_create_user();

        # Test using embedded "contents" field               
        test_username = self.user1['username']
        admin_username = self.test_admin_user['username']
        attachedfile_item_post = {
            'created_by_username': admin_username, 
            'type': '2009_iccb_l_nsrb_small_molecule_user_agreement', 
            'filename': "test_pasted_text.txt",
            'contents': "This is a test of pasted text\n1\n2\n3\n\n end\n",
            'file_date': '2015-10-10'
            }

        content_type = MULTIPART_CONTENT
        resource_uri = BASE_URI_DB + '/screensaveruser/%s/attachedfiles/' % test_username
        
        authentication=self.get_credentials()
        kwargs = {}
        kwargs['HTTP_AUTHORIZATION'] = authentication
        kwargs['HTTP_ACCEPT'] = JSON_MIMETYPE
        
        logger.info('Post attached file item: %r', attachedfile_item_post)
        django_test_client = self.api_client.client
        resp = django_test_client.post(
            resource_uri, content_type=content_type, 
            data=attachedfile_item_post, **kwargs)
        self.assertTrue(
            resp.status_code in [201, 202], 
            (resp.status_code,self.get_content(resp)))
        
        data_for_get = { 'limit': 0, 'includes': ['*'] }
        resp = self.api_client.get(
            resource_uri,
            authentication=self.get_credentials(), data=data_for_get )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        logger.info('new obj: %s ' % new_obj)
        result,msgs = assert_obj1_to_obj2(
            attachedfile_item_post, new_obj['objects'][0], 
            excludes=['contents'])
        self.assertTrue(result,msgs)
        af = new_obj['objects'][0]
        uri = '/db/attachedfile/%s/content' % af['attached_file_id']
        try:
            admin_user = User.objects.get(username=admin_username)
            view, args, kwargs = resolve(uri)
            kwargs['request'] = self.api_client.client.request()
            kwargs['request'].user=admin_user
            result = view(*args, **kwargs)
            logger.info('attached_file request view result: %r',result)
            self.assertEqual(
                result.content, attachedfile_item_post['contents'], 
                'download file view returns wrong contents: %r' 
                    % result.content)
        except Exception, e:
            logger.info('no file found at: %r', uri)
            raise
    
    def test3a_attached_file_filesystem(self):
        
        logger.info('test3a_attached_file_filesystem...')
        
        self.test0_create_user();

        # Test using embedded "contents" field               
        test_username = self.user1['username']
        admin_username = self.test_admin_user['username']
        attachedfile_item_post = {
            'created_by_username': admin_username, 
            'type': '2009_iccb_l_nsrb_small_molecule_user_agreement', 
        }

        content_type = MULTIPART_CONTENT
        resource_uri = \
            BASE_URI_DB + '/screensaveruser/%s/attachedfiles/' % test_username
        authentication=self.get_credentials()
        kwargs = {}
        kwargs['HTTP_AUTHORIZATION'] = authentication
        kwargs['HTTP_ACCEPT'] = JSON_MIMETYPE
        file = 'iccbl_sm_user_agreement_march2015.pdf'
        filename = \
            '%s/db/static/test_data/useragreement/%s' %(APP_ROOT_DIR,file)
        with open(filename) as input_file:

            logger.info('POST with attached_file to the server')
            attachedfile_item_post['attached_file'] = input_file

            logger.info('Post attached file %r', filename)
            django_test_client = self.api_client.client
            resp = django_test_client.post(
                resource_uri, content_type=content_type, 
                data=attachedfile_item_post, **kwargs)
            self.assertTrue(
                resp.status_code in [201, 202], 
                (resp.status_code,self.get_content(resp)))
        
        data_for_get = { 'limit': 0, 'includes': ['*'] }
        resp = self.api_client.get(
            resource_uri,
            authentication=self.get_credentials(), data=data_for_get )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        logger.info('new obj: %s ' % new_obj)
        result,msgs = assert_obj1_to_obj2(
            attachedfile_item_post, new_obj['objects'][0], 
            excludes=['attached_file'])
        self.assertTrue(result,msgs)
        af = new_obj['objects'][0]
        uri = '/db/attachedfile/%s/content' % af['attached_file_id']
        try:
            admin_user = User.objects.get(username=admin_username)
            view, args, kwargs = resolve(uri)
            kwargs['request'] = self.api_client.client.request()
            kwargs['request'].user=admin_user
            result = view(*args, **kwargs)
            logger.info('attached_file request view result: %r',result)
            output_filename = '%s.out.%s' % tuple(filename.split('.'))
            logger.info('write %s to %r', filename, output_filename)
            with open(output_filename, 'w') as out_file:
                out_file.write(self.get_content(result))
            self.assertTrue(filecmp.cmp(filename,output_filename), 
                'input file: %r, not equal to output file: %r' 
                % (filename, output_filename))
            os.remove(output_filename)    
        except Exception, e:
            logger.info('no file found at: %r', uri)
            raise
        
        # TODO: delete attached file
        # TODO: attachedfile logs
    
    def test4_user_agreement_updator(self):
        
        logger.info('test4_user_agreement_updator...')
        self.test0_create_user();
        group_patch = { 'objects': [
            { 'name': 'smDsl1MutualScreens' },
            { 'name': 'smDsl2MutualPositives' },
            { 'name': 'smDsl3SharedScreens' },
            { 'name': 'rnaiDsl1MutualScreens' },
            { 'name': 'rnaiDsl2MutualPositives' },
            { 'name': 'rnaiDsl3SharedScreens' },
        ]}
        resource_uri = BASE_REPORTS_URI + '/usergroup/'
        resp = self.api_client.put(
            resource_uri, 
            format='json', data=group_patch, 
            authentication=self.get_credentials())
        self.assertTrue(
            resp.status_code in [200,201,202], 
            (resp.status_code, self.get_content(resp)))
        
        test_username = self.user1['username']
        admin_username = self.test_admin_user['username']
        first_usergroup_to_add = 'smDsl2MutualPositives'
        # TEST 1 - Try adding a SM dsl
        
        useragreement_item_post = {
            'admin_user': admin_username,
            'created_by_username': admin_username, 
            'type': '2009_iccb_l_nsrb_small_molecule_user_agreement', 
            'usergroup': first_usergroup_to_add
            }
        test_comment = 'test update comment for user agreement'
        content_type = MULTIPART_CONTENT
        resource_uri = \
            BASE_URI_DB + '/screensaveruser/%s/useragreement/' % test_username
        
        authentication=self.get_credentials()
        kwargs = { 'limit': 0, 'includes': ['*'] }
        kwargs['HTTP_AUTHORIZATION'] = authentication
        kwargs[HEADER_APILOG_COMMENT] = test_comment
        
        file = 'iccbl_sm_user_agreement_march2015.pdf'
        filename = \
            '%s/db/static/test_data/useragreement/%s' %(APP_ROOT_DIR,file)
        with open(filename) as input_file:

            logger.info('PUT user agreement to the server...')
            useragreement_item_post['attached_file'] = input_file
            
            django_test_client = self.api_client.client
            resp = django_test_client.post(
                resource_uri, content_type=MULTIPART_CONTENT, 
                data=useragreement_item_post, **kwargs)
            if resp.status_code not in [201]:
                logger.info(
                    'resp code: %d, resp: %r, content: %r', 
                    resp.status_code, resp, resp.content)
            self.assertTrue(
                resp.status_code in [201], 
                (resp.status_code))
        
        # Tests: 
        
        # - check that the user agreement is an attached file to the user
        
        data_for_get = { 
            'limit': 0, 'includes': ['*'],
            'type__eq': useragreement_item_post['type']
        }
        resp = self.api_client.get(
            resource_uri,
            authentication=self.get_credentials(), data=data_for_get )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        logger.info('new obj: %s ' % new_obj)
        af = new_obj['objects'][0]
        uri = '/db/attachedfile/%s/content' % af['attached_file_id']
        try:
            admin_user = User.objects.get(username=admin_username)
            view, args, kwargs = resolve(uri)
            kwargs['request'] = self.api_client.client.request()
            kwargs['request'].user=admin_user
            result = view(*args, **kwargs)
            output_filename = '%s.out.%s' % tuple(filename.split('.'))
            logger.info('write %s to %r', filename, output_filename)
            with open(output_filename, 'w') as out_file:
                out_file.write(self.get_content(result))
            self.assertTrue(filecmp.cmp(filename,output_filename), 
                'input file: %r, not equal to output file: %r' 
                % (filename, output_filename))    
        except Exception, e:
            logger.exception('no file found at: %r', uri)
            raise
        
        # - check that the data sharing level group is assigned to the user
        
        resource_uri = BASE_URI_DB + '/screensaveruser'
        resource_uri = '/'.join([resource_uri,self.user1['username']])
        user_data = self.get_single_resource(resource_uri)
        logger.info('new user: %r', user_data)
        self.assertTrue('usergroups' in user_data)
        self.assertTrue(first_usergroup_to_add in user_data['usergroups'], 
            'usergroups returned: %r does not contain %r' 
            % (user_data['usergroups'], first_usergroup_to_add))

        # - check that a checklist item has been created for the user agreement
        
        resource_uri = BASE_URI_DB + '/userchecklistitem'
        resource_uri = '/'.join([resource_uri,self.user1['username']])
        checklist_items = self.get_list_resource(
            resource_uri, {'status__eq': 'completed'})
        logger.info('checklist_items: %r', checklist_items)
        self.assertTrue(len(checklist_items)==1)
        val = 'current_small_molecule_user_agreement_active'
        self.assertTrue(checklist_items[0]['item_name'] == val,
            'wrong checklist item - expected name: %r, %r'
            %(val, checklist_items[0]))
        
        # - check logs
        
        resource_uri = BASE_REPORTS_URI + '/apilog'
        data_for_get={ 
            'limit': 0, 
            'ref_resource_name': 'screensaveruser', 
            'key': self.user1['username'],
            'diff_keys__contains': 'data_sharing_level' 
        }
        apilogs = self.get_list_resource(
            resource_uri, data_for_get=data_for_get )
        logger.info('logs: %r', apilogs)
        self.assertTrue(
            len(apilogs) == 1, 'too many apilogs found: %r' % apilogs)
        apilog = apilogs[0]
        self.assertTrue(apilog['comment']==test_comment,
            'comment %r should be: %r' % (apilog['comment'], test_comment))
        self.assertTrue('data_sharing_level' in apilog['diff_keys'])
        self.assertTrue(first_usergroup_to_add in apilog['diffs'])

        # TEST 2 - Try adding an RNAi dsl
        second_usergroup_to_add = 'rnaiDsl1MutualScreens'
        useragreement_item_post = {
            'admin_user': admin_username,
            'created_by_username': admin_username, 
            'type': 'iccb_l_nsrb_rnai_user_agreement', 
            'usergroup': second_usergroup_to_add
            }
        test_comment = 'test update rna comment for user agreement'
        content_type = MULTIPART_CONTENT
        resource_uri = \
            BASE_URI_DB + '/screensaveruser/%s/useragreement/' % test_username
        
        authentication=self.get_credentials()
        kwargs = { 'limit': 0, 'includes': ['*'] }
        kwargs['HTTP_AUTHORIZATION'] = authentication
        kwargs[HEADER_APILOG_COMMENT] = test_comment
        
        file = 'iccbl_rnai_ua_march2015.pdf'
        filename = \
            '%s/db/static/test_data/useragreement/%s' %(APP_ROOT_DIR,file)
        with open(filename) as input_file:

            logger.info('PUT user agreement to the server...')
            useragreement_item_post['attached_file'] = input_file
            
            django_test_client = self.api_client.client
            resp = django_test_client.post(
                resource_uri, content_type=MULTIPART_CONTENT, 
                data=useragreement_item_post, **kwargs)
            if resp.status_code not in [201]:
                logger.info(
                    'resp code: %d, resp: %r, content: %r', 
                    resp.status_code, resp, resp.content)
            self.assertTrue(
                resp.status_code in [201], 
                (resp.status_code))
        
        # TEST 2 - check that the user agreement is an attached file to the user
        
        data_for_get = { 
            'limit': 0, 'includes': ['*'], 
            'type__eq': useragreement_item_post['type']}
        resp = self.api_client.get(
            resource_uri,
            authentication=self.get_credentials(), data=data_for_get )
        self.assertTrue(
            resp.status_code in [200], 
            (resp.status_code, self.get_content(resp)))
        new_obj = self.deserialize(resp)
        logger.info('new obj: %s ' % new_obj)
        self.assertTrue(
            len(new_obj['objects'])==1, 
            'too many UAs returned: %r' % new_obj)
        
        af = new_obj['objects'][0]
        uri = '/db/attachedfile/%s/content' % af['attached_file_id']
        try:
            admin_user = User.objects.get(username=admin_username)
            view, args, kwargs = resolve(uri)
            kwargs['request'] = self.api_client.client.request()
            kwargs['request'].user=admin_user
            result = view(*args, **kwargs)
            output_filename = '%s.out.%s' % tuple(filename.split('.'))
            logger.info('write %s to %r', filename, output_filename)
            with open(output_filename, 'w') as out_file:
                out_file.write(self.get_content(result))
            self.assertTrue(filecmp.cmp(filename,output_filename), 
                'input file: %r, not equal to output file: %r' 
                % (filename, output_filename))    
        except Exception, e:
            logger.exception('no file found at: %r', uri)
            raise

        # TEST 2 - check that the data sharing level group is assigned to user
        
        resource_uri = BASE_URI_DB + '/screensaveruser'
        resource_uri = '/'.join([resource_uri,self.user1['username']])
        user_data = self.get_single_resource(resource_uri)
        logger.info('user: %r', user_data)
        self.assertTrue('usergroups' in user_data)
        self.assertTrue(first_usergroup_to_add in user_data['usergroups'], 
            'usergroups returned: %r does not contain %r' 
            % (user_data['usergroups'], first_usergroup_to_add))

        self.assertTrue(second_usergroup_to_add in user_data['usergroups'], 
            'usergroups returned: %r does not contain %r' 
            % (user_data['usergroups'], second_usergroup_to_add))

        # TEST 2 - check that checklist item has been created for user agreement
        
        resource_uri = BASE_URI_DB + '/userchecklistitem'
        resource_uri = '/'.join([resource_uri,self.user1['username']])
        checklist_items = self.get_list_resource(resource_uri, 
            {'status__eq': 'completed',
             'item_name__eq': u'current_rnai_user_agreement_active'})
        logger.info('checklist_items: %r', checklist_items)
        self.assertTrue(len(checklist_items)==1)
        val = 'current_rnai_user_agreement_active'
        self.assertTrue(checklist_items[0]['item_name'] == val,
            'wrong checklist item - expected name: %r, %r'
            %(val, checklist_items[0]))
        
        # TEST 2 - check logs
        
        resource_uri = BASE_REPORTS_URI + '/apilog'
        data_for_get={ 
            'limit': 0, 
            'ref_resource_name': 'screensaveruser', 
            'key': self.user1['username'],
            'diff_keys__contains': 'data_sharing_level',
            'order_by': 'date_created' 
        }
        apilogs = self.get_list_resource(
            resource_uri, data_for_get=data_for_get )
        logger.info('logs: %r', apilogs)
        self.assertTrue(
            len(apilogs) == 2, 'too many apilogs found: %r' % apilogs)
        apilog = apilogs[1]
        self.assertTrue(apilog['comment']==test_comment,
            'comment %r should be: %r' % (apilog['comment'], test_comment))
        self.assertTrue('data_sharing_level' in apilog['diff_keys'])
        self.assertTrue(first_usergroup_to_add in apilog['diffs'])
        self.assertTrue(second_usergroup_to_add in apilog['diffs'])
    
    # TODO: test remove dsl: create a "UserAgreementResource.delete_detail"
    
    
    def test5_service_activity(self):
        
        logger.info('test5_service_activity...')
        self.test0_create_user();
        
        test_username = self.user1['username']
        admin_username = self.test_admin_user['username']
        service_activity_post = {
            'serviced_username': test_username,
            'type': "image_analysis",
            'comments': "test",
            'date_of_activity': "2015-10-27",
            'funding_support': "clardy_grants",
            'performed_by_username': admin_username,
        }

        
        try:       
            resource_uri = BASE_URI_DB + '/serviceactivity'
            resp = self.api_client.post(
                resource_uri, 
                format='json', 
                data=service_activity_post, 
                authentication=self.get_credentials())
            self.assertTrue(
                resp.status_code in [200,201,202], 
                (resp.status_code, self.get_content(resp)))
    
            data_for_get = { 'limit': 0, 'includes': ['*'] }
            resp = self.api_client.get(
                resource_uri,
                format='json', 
                authentication=self.get_credentials(), data=data_for_get )
            self.assertTrue(
                resp.status_code in [200], 
                (resp.status_code, self.get_content(resp)))
            new_obj = self.deserialize(resp)
            result,msgs = assert_obj1_to_obj2(
                service_activity_post, new_obj['objects'][0])
            self.assertTrue(result,msgs)
        
        except Exception, e:
            logger.exception('on serviceactivity test')
            raise e
        
        # TODO: delete serivceactivity
        # TODO: serviceactivity logs
