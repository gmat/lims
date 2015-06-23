# This is an auto-generated Django model module.
# You'll have to do the following manually to clean this up:
#     * Rearrange models' order
#     * Make sure each model has one field with primary_key=True
# Feel free to rename the models, but don't rename db_table values or field names.
#
# Also note: You'll have to insert the output of 'django-admin.py sqlcustom [appname]'
# into your database.





from __future__ import unicode_literals
from django.conf import settings
from django.db import connection
from django.db import models
from django.db.models.query import QuerySet
from django.db.models.sql.compiler import SQLCompiler
from django.db.models.sql.query import Query
from django.utils import timezone
from reports.utils.gray_codes import create_substance_id
import datetime
import logging
logger = logging.getLogger(__name__)


class LegacySmallMoleculeCasNumber(models.Model):
    smiles = models.CharField(max_length=2047)
    cas_number = models.TextField()
    class Meta:
        db_table = '_legacy_small_molecule_cas_number'

class AbaseTestset(models.Model):
    abase_testset_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    screen = models.ForeignKey('Screen')
    testset_name = models.TextField()
    comments = models.TextField()
    testset_date = models.DateField()
    class Meta:
        db_table = 'abase_testset'



class Activity(models.Model):
    activity_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    date_created = models.DateTimeField()
    comments = models.TextField(blank=True)
    performed_by = models.ForeignKey('ScreensaverUser',related_name='activities_performed')
    date_of_activity = models.DateField()
    created_by = models.ForeignKey('ScreensaverUser', null=True, blank=True, related_name='activities_created')
    date_loaded = models.DateTimeField(null=True, blank=True)
    date_publicly_available = models.DateTimeField(null=True, blank=True)
    class Meta:
        db_table = 'activity'

class ActivityUpdateActivity(models.Model):
    activity = models.ForeignKey(Activity)
    update_activity = models.ForeignKey('AdministrativeActivity')
    class Meta:
        db_table = 'activity_update_activity'

class AdministrativeActivity(models.Model):
    activity = models.ForeignKey(Activity, primary_key=True)
    administrative_activity_type = models.TextField()
    class Meta:
        db_table = 'administrative_activity'

class AttachedFileUpdateActivity(models.Model):
    attached_file = models.ForeignKey('AttachedFile')
    update_activity = models.ForeignKey(AdministrativeActivity)
    class Meta:
        db_table = 'attached_file_update_activity'

class ChecklistItemEventUpdateActivity(models.Model):
    checklist_item_event = models.ForeignKey('ChecklistItemEvent')
    update_activity = models.ForeignKey(AdministrativeActivity)
    class Meta:
        db_table = 'checklist_item_event_update_activity'

class CopyUpdateActivity(models.Model):
    copy_id = models.IntegerField()
    update_activity_id = models.IntegerField(unique=True)
    class Meta:
        db_table = 'copy_update_activity'

class LabActivity(models.Model):
    screen = models.ForeignKey('Screen')
    activity = models.ForeignKey(Activity, primary_key=True)
    volume_transferred_per_well_from_library_plates = models.DecimalField(null=True, max_digits=10, decimal_places=9, blank=True)
    molar_concentration = models.DecimalField(null=True, max_digits=13, decimal_places=12, blank=True)
    class Meta:
        db_table = 'lab_activity'

# TODO consider how can refactor Screening-[libraryscreening, cherrypickscreening] 
# hierarchy
class LibraryScreening(models.Model):
    abase_testset_id = models.TextField(blank=True)
    is_for_external_library_plates = models.BooleanField()
    activity = models.ForeignKey('Screening', primary_key=True)
    screened_experimental_well_count = models.IntegerField()
    libraries_screened_count = models.IntegerField(null=True, blank=True)
    library_plates_screened_count = models.IntegerField(null=True, blank=True)
    class Meta:
        db_table = 'library_screening'

class Screening(models.Model):
    assay_protocol = models.TextField(blank=True)
    number_of_replicates = models.IntegerField(null=True, blank=True)
    assay_protocol_type = models.TextField(blank=True)
    activity = models.ForeignKey(LabActivity, primary_key=True)
    assay_protocol_last_modified_date = models.DateField(null=True, blank=True)
    assay_well_volume = models.DecimalField(null=True, max_digits=10, decimal_places=9, blank=True)
    volume_transferred_per_well_to_assay_plates = models.DecimalField(null=True, max_digits=10, decimal_places=9, blank=True)
    class Meta:
        db_table = 'screening'

class EquipmentUsed(models.Model):
    equipment_used_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    protocol = models.TextField(blank=True)
    description = models.TextField(blank=True)
    equipment = models.TextField()
    lab_activity = models.ForeignKey('LabActivity')
    class Meta:
        db_table = 'equipment_used'

class LibraryUpdateActivity(models.Model):
    library = models.ForeignKey('Library')
    update_activity = models.ForeignKey(AdministrativeActivity)
    class Meta:
        db_table = 'library_update_activity'

class PlateUpdateActivity(models.Model):
    plate_id = models.IntegerField()
    update_activity_id = models.IntegerField(unique=True)
    class Meta:
        db_table = 'plate_update_activity'

class ScreenResultUpdateActivity(models.Model):
    screen_result = models.ForeignKey('ScreenResult')
    update_activity = models.ForeignKey(AdministrativeActivity)
    class Meta:
        db_table = 'screen_result_update_activity'

class ScreenUpdateActivity(models.Model):
    screen = models.ForeignKey('Screen')
    update_activity = models.ForeignKey(AdministrativeActivity)
    class Meta:
        db_table = 'screen_update_activity'

class ScreensaverUserUpdateActivity(models.Model):
    screensaver_user = models.ForeignKey('ScreensaverUser')
    update_activity = models.ForeignKey(AdministrativeActivity)
    class Meta:
        db_table = 'screensaver_user_update_activity'

class ServiceActivity(models.Model):
    service_activity_type = models.TextField()
    activity = models.ForeignKey(Activity, primary_key=True)
    serviced_screen = models.ForeignKey('Screen', null=True, blank=True)
    serviced_user = models.ForeignKey('ScreeningRoomUser')
    class Meta:
        db_table = 'service_activity'

class AnnotationType(models.Model):
    annotation_type_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    study = models.ForeignKey('Screen')
    name = models.TextField(blank=True)
    description = models.TextField(blank=True)
    ordinal = models.IntegerField()
    is_numeric = models.BooleanField()
    class Meta:
        db_table = 'annotation_type'

class AnnotationValue(models.Model):
    annotation_value_id = models.IntegerField(null=True, blank=True)
    numeric_value = models.FloatField(null=True, blank=True)
    value = models.TextField(blank=True)
    annotation_type = models.ForeignKey(AnnotationType, null=True, blank=True)
    reagent = models.ForeignKey('Reagent', null=True, blank=True)
    class Meta:
        db_table = 'annotation_value'

class AssayPlate(models.Model):
    assay_plate_id = models.IntegerField(primary_key=True)
    replicate_ordinal = models.IntegerField()
    version = models.IntegerField()
    screen = models.ForeignKey('Screen')
    plate = models.ForeignKey('Plate', null=True, blank=True)
    plate_number = models.IntegerField()

    library_screening = models.ForeignKey('LibraryScreening', null=True, blank=True)
    screen_result_data_loading = models.ForeignKey(AdministrativeActivity, null=True, blank=True)
    
    class Meta:
        db_table = 'assay_plate'
    def __unicode__(self):
        return unicode(str((self.plate.copy.name, self.plate_number, self.replicate_ordinal)))
        
class AssayWell(models.Model):
    assay_well_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    assay_well_control_type = models.TextField(blank=True)
    is_positive = models.BooleanField()
    screen_result = models.ForeignKey('ScreenResult')
    well = models.ForeignKey('Well')
    confirmed_positive_value = models.TextField(blank=True)
    class Meta:
        db_table = 'assay_well'

class AttachedFile(models.Model):
    attached_file_id = models.IntegerField(primary_key=True)
    date_created = models.DateTimeField()
    file_contents = models.TextField() # This field type is a guess.
    filename = models.TextField()
    version = models.IntegerField()
    screen = models.ForeignKey('Screen', null=True, blank=True)
    screensaver_user = models.ForeignKey('ScreeningRoomUser', null=True, blank=True)
    attached_file_type = models.ForeignKey('AttachedFileType')
    created_by = models.ForeignKey('ScreensaverUser', null=True, blank=True)
    reagent = models.ForeignKey('Reagent', null=True, blank=True)
    file_date = models.DateField(null=True, blank=True)
    date_loaded = models.DateTimeField(null=True, blank=True)
    date_publicly_available = models.DateTimeField(null=True, blank=True)
    class Meta:
        db_table = 'attached_file'

class AttachedFileType(models.Model):
    for_entity_type = models.CharField(max_length=31)
    attached_file_type_id = models.IntegerField(primary_key=True)
    value = models.TextField()
    class Meta:
        db_table = 'attached_file_type'

class CellLine(models.Model):
    cell_line_id = models.IntegerField(primary_key=True)
    value = models.TextField(unique=True)
    version = models.IntegerField()
    class Meta:
        db_table = 'cell_line'
        
class ChecklistItem(models.Model):
    checklist_item_id = models.IntegerField(primary_key=True)
    checklist_item_group = models.TextField()
    is_expirable = models.BooleanField()
    item_name = models.TextField(unique=True)
    order_statistic = models.IntegerField()
    version = models.IntegerField()
    class Meta:
        db_table = 'checklist_item'

class ChecklistItemEvent(models.Model):
    checklist_item_event_id = models.IntegerField(primary_key=True)
    date_performed = models.DateField(null=True, blank=True)
    is_expiration = models.BooleanField()
    checklist_item_id = models.IntegerField()
    screening_room_user = models.ForeignKey('ScreeningRoomUser')
    is_not_applicable = models.BooleanField()
    date_created = models.DateTimeField()
    created_by = models.ForeignKey('ScreensaverUser', null=True, blank=True)
    date_loaded = models.DateTimeField(null=True, blank=True)
    date_publicly_available = models.DateTimeField(null=True, blank=True)
    class Meta:
        db_table = 'checklist_item_event'

class CherryPickRequest(models.Model):
    cherry_pick_request_id = models.IntegerField(primary_key=True)
    # TODO: give cpr a natural key: [screen id/CPR ordinal]
    version = models.IntegerField()
    screen = models.ForeignKey('Screen')
    comments = models.TextField(blank=True)
    requested_by = models.ForeignKey('ScreeningRoomUser')
    is_randomized_assay_plate_layout = models.BooleanField()
    legacy_cherry_pick_request_number = models.IntegerField(null=True, blank=True)
    volume_approved_by = models.ForeignKey('AdministratorUser', null=True, blank=True)
    number_unfulfilled_lab_cherry_picks = models.IntegerField()
    assay_plate_type = models.TextField()
    transfer_volume_per_well_approved = models.DecimalField(null=True, max_digits=10, decimal_places=9, blank=True)
    transfer_volume_per_well_requested = models.DecimalField(null=True, max_digits=10, decimal_places=9, blank=True)
    date_requested = models.DateField()
    date_volume_approved = models.DateField(null=True, blank=True)
    assay_protocol_comments = models.TextField(blank=True)
    cherry_pick_assay_protocols_followed = models.TextField(blank=True)
    cherry_pick_followup_results_status = models.TextField(blank=True)
    date_created = models.DateTimeField()
    created_by = models.ForeignKey('ScreensaverUser', null=True, blank=True)
    keep_source_plate_cherry_picks_together = models.BooleanField()
    date_loaded = models.DateTimeField(null=True, blank=True)
    date_publicly_available = models.DateTimeField(null=True, blank=True)
    max_skipped_wells_per_plate = models.IntegerField(null=True, blank=True)
    
    class Meta:
        db_table = 'cherry_pick_request'
    def __unicode__(self):
        return unicode(str((self.screen.facility_id, self.cherry_pick_request_id))) 

class CherryPickRequestEmptyWell(models.Model):
    cherry_pick_request = models.ForeignKey(CherryPickRequest)
    well_name = models.CharField(max_length=255, blank=True)
    class Meta:
        db_table = 'cherry_pick_request_empty_well'

class LabCherryPick(models.Model):
    lab_cherry_pick_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    cherry_pick_request = models.ForeignKey('CherryPickRequest')
    screener_cherry_pick = models.ForeignKey('ScreenerCherryPick')
    source_well = models.ForeignKey('Well')
    cherry_pick_assay_plate = models.ForeignKey('CherryPickAssayPlate', null=True, blank=True)
    assay_plate_row = models.IntegerField(null=True, blank=True)
    assay_plate_column = models.IntegerField(null=True, blank=True)
    class Meta:
        db_table = 'lab_cherry_pick'

class CherryPickAssayPlate(models.Model):
    cherry_pick_assay_plate_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    cherry_pick_request = models.ForeignKey('CherryPickRequest')
    plate_ordinal = models.IntegerField()
    attempt_ordinal = models.IntegerField()
    cherry_pick_liquid_transfer = models.ForeignKey('CherryPickLiquidTransfer', null=True, blank=True)
    assay_plate_type = models.TextField()
    legacy_plate_name = models.TextField(blank=True)
    cherry_pick_assay_plate_type = models.CharField(max_length=31)
    
    status = models.TextField(null=True)
    class Meta:
        unique_together = (('cherry_pick_request', 'plate_ordinal','attempt_ordinal'))    
        db_table = 'cherry_pick_assay_plate'

class CherryPickScreening(models.Model):
    activity = models.ForeignKey('Screening', primary_key=True)
    cherry_pick_request = models.ForeignKey('CherryPickRequest')
    class Meta:
        db_table = 'cherry_pick_screening'

class CherryPickAssayPlateScreeningLink(models.Model):
    cherry_pick_assay_plate = models.ForeignKey(CherryPickAssayPlate)
    cherry_pick_screening = models.ForeignKey('CherryPickScreening')
    class Meta:
        db_table = 'cherry_pick_assay_plate_screening_link'

class CherryPickLiquidTransfer(models.Model):
    status = models.TextField()
    activity = models.ForeignKey('LabActivity', primary_key=True)
    class Meta:
        db_table = 'cherry_pick_liquid_transfer'

class CherryPickRequestUpdateActivity(models.Model):
    cherry_pick_request = models.ForeignKey('CherryPickRequest')
    update_activity = models.ForeignKey('AdministrativeActivity', unique=True)
    class Meta:
        db_table = 'cherry_pick_request_update_activity'

class CollaboratorLink(models.Model):
    collaborator = models.ForeignKey('ScreeningRoomUser')
    screen = models.ForeignKey('Screen')
    class Meta:
        db_table = 'collaborator_link'

class FundingSupport(models.Model):
    funding_support_id = models.IntegerField(primary_key=True)
    value = models.TextField(unique=True, blank=True)
    class Meta:
        db_table = 'funding_support'

class LabAffiliation(models.Model):
    version = models.IntegerField()
    affiliation_name = models.TextField(unique=True)
    affiliation_category = models.TextField()
    lab_affiliation_id = models.IntegerField(primary_key=True)
    class Meta:
        db_table = 'lab_affiliation'

class Publication(models.Model):
    publication_id = models.IntegerField(primary_key=True)
    authors = models.TextField(blank=True)
    journal = models.TextField(blank=True)
    pages = models.TextField(blank=True)
    pubmed_id = models.TextField(blank=True)
    title = models.TextField(blank=True)
    version = models.IntegerField()
    volume = models.TextField(blank=True)
    year_published = models.TextField(blank=True)
    attached_file = models.ForeignKey(AttachedFile, unique=True, null=True, blank=True)
    pubmed_central_id = models.IntegerField(null=True, blank=True)
    class Meta:
        db_table = 'publication'

class RnaiCherryPickRequest(models.Model):
    cherry_pick_request = models.ForeignKey(CherryPickRequest, primary_key=True)
    class Meta:
        db_table = 'rnai_cherry_pick_request'

class SchemaHistory(models.Model):
    screensaver_revision = models.IntegerField(primary_key=True)
    date_updated = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True)
    class Meta:
        db_table = 'schema_history'

class Screen(models.Model):
    # Note: migration scripts have converted this to use a sequence 
    # (essentially AutoField)
    #     screen_id = models.IntegerField(primary_key=True) 
    screen_id = models.AutoField(primary_key=True) 
    version = models.IntegerField()
    date_created = models.DateTimeField()
    screen_type = models.TextField(null=False, blank=False)
    title = models.TextField(null=False, blank=False)
    summary = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    abase_study_id = models.TextField(blank=True)
    abase_protocol_id = models.TextField(blank=True)
    publishable_protocol = models.TextField(blank=True)
    lead_screener = models.ForeignKey('ScreeningRoomUser', null=True, blank=True)
    lab_head = models.ForeignKey('LabHead', null=True, blank=True)
    publishable_protocol_entered_by = models.TextField(blank=True)
    publishable_protocol_comments = models.TextField(blank=True)
    study_type = models.TextField(null=False, blank=False)
    url = models.TextField(blank=True)
    data_meeting_complete = models.DateField(null=True, blank=True)
    data_meeting_scheduled = models.DateField(null=True, blank=True)
    date_of_application = models.DateField(null=True, blank=True)
    publishable_protocol_date_entered = models.DateField(null=True, blank=True)
    amount_to_be_charged_for_screen = models.DecimalField(null=True, max_digits=9, decimal_places=2, blank=True)
    billing_comments = models.TextField(blank=True)
    is_billing_for_supplies_only = models.BooleanField(default=False) # TODO: obsolete? still used?
    billing_info_return_date = models.DateField(null=True, blank=True)
    date_charged = models.DateField(null=True, blank=True)
    date_completed5kcompounds = models.DateField(null=True, blank=True)
    date_faxed_to_billing_department = models.DateField(null=True, blank=True)
    facilities_and_administration_charge = models.DecimalField(null=True, max_digits=9, decimal_places=2, blank=True)
    is_fee_form_on_file = models.BooleanField(null=False, default=False)
    fee_form_requested_date = models.DateField(null=True, blank=True)
    fee_form_requested_initials = models.TextField(blank=True)
    see_comments = models.BooleanField(default=False)
    to_be_requested = models.BooleanField(default=False)  #  TODO: obsolete? still used?
    coms_registration_number = models.TextField(blank=True)
    coms_approval_date = models.DateField(null=True, blank=True)
    pin_transfer_admin_activity = models.ForeignKey(AdministrativeActivity, null=True, blank=True)
    data_sharing_level = models.IntegerField(null=False)
    created_by = models.ForeignKey('ScreensaverUser', null=True, blank=True)
    data_privacy_expiration_date = models.DateField(null=True, blank=True)
    max_allowed_data_privacy_expiration_date = models.DateField(null=True, blank=True)
    min_allowed_data_privacy_expiration_date = models.DateField(null=True, blank=True)
    data_privacy_expiration_notified_date = models.DateField(null=True, blank=True)
    screened_experimental_well_count = models.IntegerField(null=False, default=0)
    unique_screened_experimental_well_count = models.IntegerField(null=False, default=0)
    total_plated_lab_cherry_picks = models.IntegerField(null=False, default=0)
    assay_plates_screened_count = models.IntegerField(null=False, default=0)
    library_plates_screened_count = models.IntegerField(null=False, default=0)
    library_plates_data_loaded_count = models.IntegerField(null=False, default=0)
    library_plates_data_analyzed_count = models.IntegerField(null=False, default=0)
    min_screened_replicate_count = models.IntegerField(null=True, blank=True)
    max_screened_replicate_count = models.IntegerField(null=True, blank=True)
    min_data_loaded_replicate_count = models.IntegerField(null=True, blank=True)
    max_data_loaded_replicate_count = models.IntegerField(null=True, blank=True)
    libraries_screened_count = models.IntegerField(null=True, blank=True)
    facility_id = models.TextField(unique=True)
    project_phase = models.TextField()
    project_id = models.TextField(blank=True)
    pubchem_assay_id = models.IntegerField(null=True, blank=True)
    pubchem_deposited_date = models.DateField(null=True, blank=True)
    image_url = models.TextField(blank=True)
    well_studied = models.ForeignKey('Well', null=True, blank=True)
    species = models.TextField(blank=True)
# Note: this is not in production
#     cell_line = models.ForeignKey(CellLine, null=True, blank=True) 
    transfection_agent = models.ForeignKey('TransfectionAgent', null=True, blank=True)
    date_loaded = models.DateTimeField(null=True, blank=True)
    date_publicly_available = models.DateTimeField(null=True, blank=True)
    perturbagen_molar_concentration = models.DecimalField(null=True, max_digits=13, decimal_places=12, blank=True)
    perturbagen_ug_ml_concentration = models.DecimalField(null=True, max_digits=5, decimal_places=3, blank=True)

    status = models.TextField(null=True, blank=True)
    status_date = models.DateField(null=True, blank=True)
    
    assay_type = models.TextField(null=True, blank=True)
    
    class Meta:
        db_table = 'screen'

class ScreenBillingItem(models.Model):
    screen = models.ForeignKey(Screen)
    amount = models.DecimalField(max_digits=9, decimal_places=2)
    date_sent_for_billing = models.DateField(null=True, blank=True)
    item_to_be_charged = models.TextField()
    ordinal = models.IntegerField()
    class Meta:
        db_table = 'screen_billing_item'

class ScreenFundingSupportLink(models.Model):
    screen = models.ForeignKey(Screen)
    funding_support = models.ForeignKey(FundingSupport)
    class Meta:
        db_table = 'screen_funding_support_link'

class ScreenKeyword(models.Model):
    screen = models.ForeignKey(Screen)
    keyword = models.TextField()
    class Meta:
        db_table = 'screen_keyword'

class ScreenPublicationLink(models.Model):
    screen = models.ForeignKey(Screen)
    publication_id = models.IntegerField(unique=True)
    class Meta:
        db_table = 'screen_publication_link'

class ScreenResult(models.Model):
    screen_result_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    replicate_count = models.IntegerField()
    experimental_well_count = models.IntegerField()
    screen = models.OneToOneField(Screen)
    date_created = models.DateTimeField()
    created_by = models.ForeignKey('ScreensaverUser', null=True, blank=True)
    channel_count = models.IntegerField(null=True, blank=True)
    date_loaded = models.DateTimeField(null=True, blank=True)
    date_publicly_available = models.DateTimeField(null=True, blank=True)
    class Meta:
        db_table = 'screen_result'

class ResultValue(models.Model):
    result_value_id = models.IntegerField(null=True, blank=True)
    assay_well_control_type = models.TextField(blank=True)
    is_exclude = models.NullBooleanField(null=True, blank=True)
    numeric_value = models.FloatField(null=True, blank=True)
    is_positive = models.NullBooleanField(null=True, blank=True)
    value = models.TextField(blank=True)
    data_column = models.ForeignKey('DataColumn', null=True, blank=True)
    well = models.ForeignKey('Well', null=True, blank=True)
    class Meta:
        db_table = 'result_value'

class DataColumn(models.Model):
    data_column_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    ordinal = models.IntegerField()
    replicate_ordinal = models.IntegerField(null=True, blank=True)
    assay_phenotype = models.TextField(blank=True)
    assay_readout_type = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    description = models.TextField(blank=True)
    how_derived = models.TextField(blank=True)
    is_follow_up_data = models.BooleanField()
    name = models.TextField()
    time_point = models.TextField(blank=True)
    is_derived = models.BooleanField()
    positives_count = models.IntegerField(null=True, blank=True)
    screen_result = models.ForeignKey('ScreenResult')
    channel = models.IntegerField(null=True, blank=True)
    time_point_ordinal = models.IntegerField(null=True, blank=True)
    zdepth_ordinal = models.IntegerField(null=True, blank=True)
    data_type = models.TextField()
    decimal_places = models.IntegerField(null=True, blank=True)
    strong_positives_count = models.IntegerField(null=True, blank=True)
    medium_positives_count = models.IntegerField(null=True, blank=True)
    weak_positives_count = models.IntegerField(null=True, blank=True)
    class Meta:
        db_table = 'data_column'

class DataColumnDerivedFromLink(models.Model):
    derived_data_column = models.ForeignKey(DataColumn)
    derived_from_data_column = models.ForeignKey(DataColumn, related_name='derived_from')
    class Meta:
        db_table = 'data_column_derived_from_link'


# TODO: this table is obsoleted after migration scripts 0002,0003 and 
# manual/0003_screen_status.sql are run.
class ScreenStatusItem(models.Model):
    screen = models.ForeignKey(Screen)
    status = models.TextField()
    status_date = models.DateField()
    class Meta:
        db_table = 'screen_status_item'
        index_together = (('screen', 'status','status_date'),)    

class ScreenerCherryPick(models.Model):
    screener_cherry_pick_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    cherry_pick_request = models.ForeignKey(CherryPickRequest)
    screened_well = models.ForeignKey('Well')
    class Meta:
        db_table = 'screener_cherry_pick'




class ScreensaverUser(models.Model):
#    objects = PostgresManager()

    screensaver_user_id = models.IntegerField(primary_key=True)
    version = models.IntegerField(blank=True, default=1) # TODO: legacy hibernate version attribute should go away
    date_created = models.DateTimeField()
    first_name = models.TextField()
    last_name = models.TextField()
    email = models.TextField(blank=True)
    phone = models.TextField(blank=True)
    mailing_address = models.TextField(blank=True)
    comments = models.TextField(blank=True)
    login_id = models.TextField(unique=True, blank=True)
    digested_password = models.TextField(blank=True)
    ecommons_id = models.TextField(blank=True)
    harvard_id = models.TextField(blank=True)
    harvard_id_expiration_date = models.DateField(null=True, blank=True)
    harvard_id_requested_expiration_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey('self', null=True, blank=True)
    date_loaded = models.DateTimeField(null=True, blank=True)
    date_publicly_available = models.DateTimeField(null=True, blank=True)

    # TODO: it would be nice to move user out of db
    user = models.ForeignKey('reports.UserProfile', null=True,on_delete=models.SET_NULL)

    class Meta:
        db_table = 'screensaver_user'
        
    def __unicode__(self):
        return unicode(str((
            self.screensaver_user_id, 
            self.first_name, 
            self.last_name, 
            self.email, 
            self.login_id, 
            self.ecommons_id)))

class ScreeningRoomUser(models.Model):
    screensaver_user = models.OneToOneField('ScreensaverUser', primary_key=True)
    user_classification = models.TextField()
    lab_head = models.ForeignKey('LabHead', null=True, blank=True)
    coms_crhba_permit_number = models.TextField(blank=True)
    coms_crhba_permit_principal_investigator = models.TextField(blank=True)
    last_notified_smua_checklist_item_event = models.ForeignKey(ChecklistItemEvent, null=True, blank=True, related_name='smua_user')
    last_notified_rnaiua_checklist_item_event = models.ForeignKey(ChecklistItemEvent, null=True, blank=True, related_name='rnai_ua_user')
    class Meta:
        db_table = 'screening_room_user'

class AdministratorUser(models.Model):
    screensaver_user = models.OneToOneField('ScreensaverUser', primary_key=True)
    class Meta:
        db_table = 'administrator_user'
        
class LabHead(models.Model):
#    screensaver_user = models.ForeignKey('ScreeningRoomUser', primary_key=True)
    screensaver_user = models.OneToOneField('ScreeningRoomUser', primary_key=True)
    lab_affiliation = models.ForeignKey(LabAffiliation, null=True, blank=True)
    
    class Meta:
        db_table = 'lab_head'

class ScreeningRoomUserFacilityUsageRole(models.Model):
    screening_room_user = models.ForeignKey(ScreeningRoomUser)
    facility_usage_role = models.TextField()
    class Meta:
        db_table = 'screening_room_user_facility_usage_role'
        
class ScreensaverUserRole(models.Model):
    screensaver_user = models.ForeignKey(ScreensaverUser)
    screensaver_user_role = models.TextField()
    class Meta:
        db_table = 'screensaver_user_role'




# # 20140828 - provisional, to get a substance_id_seq that is available to test environment
# # since can't figure out how to create it manually!
# class Substance(models.Model):
#     
#     id = models.AutoField(primary_key=True)
#     
#     class Meta:
#         db_table = 'substance'
#     
#     def __unicode__(self):
#         return str(self.id)    
#     
#         
# def create_id():
#     # NOTE: using a custom sequence so we don't waste by using reagent_reagent_id_seq
#     # REQUIRES: migration 0002_db_migration_prep_a
#     sequence_name = 'substance_id_seq'
#     cursor = connection.cursor()
#     cursor.execute("SELECT nextval(%s)" % sequence_name)
#     row = cursor.fetchone();
#     val = row[0]
#     new_id = create_substance_id(val)
# #     logger.info(str(('create_id', val, new_id)))
# #     cursor.execute("SELECT setval('reagent_reagent_id_seq', %s)", [val-1])
#     return new_id

# def create_id():
#     # NOTE: using a custom sequence so we don't waste by using reagent_reagent_id_seq
#     # REQUIRES: migration 0002_db_migration_prep_a
#     sequence_name = 'reagent_reagent_id_seq'
#     cursor = connection.cursor()
#     cursor.execute("SELECT currval(%s)" % sequence_name)
#     row = cursor.fetchone();
#     val = row[0]
#     new_id = create_substance_id(val)
# #     logger.info(str(('create_id', val, new_id)))
# #     cursor.execute("SELECT setval('reagent_reagent_id_seq', %s)", [val-1])
#     return new_id


def create_id():
    # TODO: There is no way to pass an argument to
    # to the default on a model, so this is why we have to grope around for the
    # sequence value.  The sequence here is used with the model idfield  
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT nextval('db_substance_id_seq')")
        row = cursor.fetchone();
        val = row[0]
    
        new_id = create_substance_id(val)
        if(logger.isEnabledFor(logging.DEBUG)):
            logger.debug(str(('created_id', val, new_id)))
    #     if val > 1:
    #         cursor.execute("SELECT setval('db_substance_id_seq', %s)", [val-1])
        return new_id
    except Exception, e:
        logger.warn(str(('create_substance_id fails', e)))
        return None

class Well(models.Model):
    well_id = models.TextField(primary_key=True)
    version = models.IntegerField(blank=True, null=True)
    plate_number = models.IntegerField()
    well_name = models.TextField()
    facility_id = models.TextField(blank=True)
    library_well_type = models.TextField()
    library = models.ForeignKey('Library')
    deprecation_admin_activity = models.ForeignKey('AdministrativeActivity', null=True, blank=True)
    is_deprecated = models.BooleanField(default=False)
    latest_released_reagent = models.ForeignKey('Reagent', null=True, blank=True, related_name='reagent_well')
    
#     reagent = models.ForeignKey('Reagent', null=True, related_name='wells')
#     reagent = models.ForeignKey('Reagent', to_field='substance_id')
    
#     molar_concentration = models.DecimalField(null=True, max_digits=13, decimal_places=12, blank=True)
#     mg_ml_concentration = models.DecimalField(null=True, max_digits=5, decimal_places=3, blank=True)
    molar_concentration = models.FloatField(null=True, blank=True)
#     micro_molar_concentration = models.FloatField(null=True, blank=True)
    mg_ml_concentration = models.FloatField(null=True, blank=True)
    
    barcode = models.TextField(null=True, unique=True)
    
    class Meta:
        db_table = 'well'
    def __unicode__(self):
        return unicode(str(self.well_id)) 
        
        
class CachedQuery(models.Model):
    ''' For caching large resultvalue queries '''
    # unique hash of the query sql
    key = models.TextField(unique=True)
    # query sql
    sql = models.TextField()
    # resource uri
    uri = models.TextField(null=False)
    # query params
    params = models.TextField(null=True)
    
    datetime = models.DateTimeField(default=timezone.now)
    username = models.CharField(null=False, max_length=128)
    count = models.IntegerField(null=True)
    
    class Meta:
#         unique_together = (('sql', 'key'))   
        db_table = 'cached_query' 

    def __unicode__(self):
        return unicode(str((self.id, self.uri,self.username,self.count))) 

# Note: this model is not in the Django models, as it does not have the id field needed
# class WellQueryIndex(models.Model):
#     ''' For caching large resultvalue queries '''
#     
#     well = models.ForeignKey('Well', null=False)
#     query = models.ForeignKey('CachedQuery', null=False)
#     class Meta:
#         db_table = 'well_query_index'
        
# create a substance table, as an easy way of creating the db_substance_id_seq
class Substance(models.Model):
    comment = models.TextField()
    def __unicode__(self):
        return unicode(str(self.id)) 

class Reagent(models.Model):
    # Note: migration scripts have converted this to use a sequence 
    # (essentially AutoField)
    #     reagent_id = models.IntegerField(primary_key=True)
    reagent_id = models.AutoField(primary_key=True)
    
    substance_id = models.CharField(
        max_length=8, unique=True, 
        default=create_id)
    
    vendor_identifier = models.TextField(blank=True)
    vendor_name = models.TextField(blank=True)
    library_contents_version = models.ForeignKey('LibraryContentsVersion', null=True)
#     library = models.ForeignKey('Library', null=True)

    # FIXME: need to create a migration script that will invalidate all of the
    # reagent.well_id's for reagents other than the "latest released reagent"
    well = models.ForeignKey('Well', null=True) # , related_name='well_reagent')
#     facility_batch_id = models.IntegerField(null=True, blank=True)
    vendor_batch_id = models.TextField(blank=True)
    class Meta:
        db_table = 'reagent'

class ReagentPublicationLink(models.Model):
    reagent = models.ForeignKey(Reagent)
    publication_id = models.IntegerField(unique=True)
    class Meta:
        db_table = 'reagent_publication_link'

class SilencingReagent(models.Model):
    reagent = models.OneToOneField(Reagent, primary_key=True)
    sequence = models.TextField(blank=True)
    anti_sense_sequence = models.TextField(blank=True)
    silencing_reagent_type = models.TextField(blank=True)
    vendor_gene = models.ForeignKey(
        'Gene', unique=True, null=True, blank=True, related_name='vendor_reagent')
    facility_gene = models.ForeignKey(
        'Gene', unique=True, null=True, blank=True, related_name='facility_reagent')
    
    duplex_wells = models.ManyToManyField('Well')
    is_restricted_sequence = models.BooleanField(default=False)
    class Meta:
        db_table = 'silencing_reagent'

class Gene(models.Model):
    gene_id = models.AutoField(primary_key=True)
    entrezgene_id = models.IntegerField(null=True, blank=True)
    gene_name = models.TextField(blank=True)
    species_name = models.TextField(blank=True)
    class Meta:
        db_table = 'gene'

    def __unicode__(self):
        return unicode(str((self.entrezgene_id, self.gene_name)))

# class ReagentFacilityGenes(models.Model):
#     reagent = models.ForeignKey(SilencingReagent, primary_key=True)
#     gene = models.ForeignKey(Gene, unique=True)
#     ordinal = models.IntegerField()
#     class Meta:
#         managed = False
#         db_table = 'reagent_facility_genes'
#  
# class ReagentVendorGenes(models.Model):
#     reagent = models.ForeignKey(SilencingReagent, primary_key=True)
#     gene = models.ForeignKey(Gene, unique=True)
#     ordinal = models.IntegerField()
#     class Meta:
#         managed = False
#         db_table = 'reagent_vendor_genes'

class GeneGenbankAccessionNumber(models.Model):
    gene = models.ForeignKey(Gene)
    genbank_accession_number = models.TextField()
    class Meta:
        unique_together = (('gene', 'genbank_accession_number'))    
        db_table = 'gene_genbank_accession_number'

# class GeneOldEntrezgeneId(models.Model):
#     old_entrezgene_id = models.IntegerField()
#     gene_id = models.IntegerField()
#     class Meta:
#         db_table = 'gene_old_entrezgene_id'
# 
# class GeneOldEntrezgeneSymbol(models.Model):
#     old_entrezgene_symbol = models.TextField()
#     gene_id = models.IntegerField()
#     class Meta:
#         db_table = 'gene_old_entrezgene_symbol'

class GeneSymbol(models.Model):
    gene = models.ForeignKey(Gene)
    entrezgene_symbol = models.TextField()
    ordinal = models.IntegerField()
    class Meta:
        unique_together = (('gene', 'ordinal'))    
        db_table = 'gene_symbol'

# Django creates this when it creates the many-to-many relationship 
# class SilencingReagentDuplexWells(models.Model):
#     silencing_reagent = models.ForeignKey(SilencingReagent)
#     well = models.ForeignKey('Well')
#     class Meta:
#         unique_together = (('silencing_reagent', 'well'))    
#         db_table = 'silencing_reagent_duplex_wells'

class SmallMoleculeReagent(models.Model):
    reagent = models.OneToOneField(Reagent, primary_key=True)
    inchi = models.TextField(blank=True)
    molecular_formula = models.TextField(blank=True)
#     molecular_mass = models.DecimalField(null=True, max_digits=15, decimal_places=9, blank=True)
#     molecular_weight = models.DecimalField(null=True, max_digits=15, decimal_places=9, blank=True)
    molecular_mass = models.FloatField(null=True, blank=True)
    molecular_weight = models.FloatField(null=True, blank=True)
    smiles = models.TextField(blank=True)
#     salt_form_id = models.IntegerField(null=True, blank=True)
    is_restricted_structure = models.BooleanField(default=False)
    class Meta:
        db_table = 'small_molecule_reagent'

class Molfile(models.Model):
    molfile = models.TextField()
#     ordinal = models.IntegerField() # TODO: legacy, get rid of this
    reagent = models.OneToOneField('Reagent', unique=True, primary_key=True)
    class Meta:
        db_table = 'molfile'

class NaturalProductReagent(models.Model):
    reagent = models.OneToOneField(Reagent, primary_key=True)
    class Meta:
        db_table = 'natural_product_reagent'

class StudyReagentLink(models.Model):
    study = models.ForeignKey(Screen)
    reagent = models.ForeignKey(Reagent)
    class Meta:
        db_table = 'study_reagent_link'

#class SilencingReagentNonTargettedGenbankAccessionNumber(models.Model):
#    silencing_reagent_id = models.TextField()
#    non_targetted_genbank_accession_number = models.TextField()
#    class Meta:
#        db_table = 'silencing_reagent_non_targetted_genbank_accession_number'

class SmallMoleculeChembankId(models.Model):
    reagent = models.ForeignKey('Reagent')
    chembank_id = models.IntegerField()
    class Meta:
        db_table = 'small_molecule_chembank_id'

class SmallMoleculeChemblId(models.Model):
    reagent = models.ForeignKey('Reagent')
    chembl_id = models.IntegerField()
    class Meta:
        db_table = 'small_molecule_chembl_id'

class SmallMoleculeCompoundName(models.Model):
    reagent = models.ForeignKey('Reagent')
    compound_name = models.TextField()
    ordinal = models.IntegerField()
    class Meta:
        db_table = 'small_molecule_compound_name'

class SmallMoleculePubchemCid(models.Model):
    reagent = models.ForeignKey('Reagent')
    pubchem_cid = models.IntegerField()
    class Meta:
        db_table = 'small_molecule_pubchem_cid'


class SmallMoleculeCherryPickRequest(models.Model):
    cherry_pick_request = models.ForeignKey(CherryPickRequest, primary_key=True)
    class Meta:
        db_table = 'small_molecule_cherry_pick_request'




class TransfectionAgent(models.Model):
    transfection_agent_id = models.IntegerField(primary_key=True)
    value = models.TextField(unique=True)
    version = models.IntegerField()
    class Meta:
        db_table = 'transfection_agent'




class Library(models.Model):
    # Note: migration scripts have converted this to use a sequence
    # (the original db-discovery only knew that it was the pk)
    #     library_id = models.IntegerField(primary_key=True) 
    library_id = models.AutoField(primary_key=True) 
#     version = models.IntegerField()
    version = models.IntegerField(blank=True, null=True)
    library_name = models.TextField(unique=True)
    short_name = models.TextField(unique=True)
    description = models.TextField(blank=True)
    provider = models.TextField(blank=True)
    screen_type = models.TextField()
    library_type = models.TextField()
    start_plate = models.IntegerField(unique=True)
    end_plate = models.IntegerField(unique=True)
    screening_status = models.TextField()
    date_received = models.DateField(null=True, blank=True)
    date_screenable = models.DateField(null=True, blank=True)
    date_created = models.DateTimeField()
    plate_size = models.TextField()

    # FIXME: need to create a migration script that will invalidate all of the
    # reagent.well_id's for reagents other than the "latest released reagent"
    latest_released_contents_version_id = models.IntegerField(null=True, blank=True)
    
    experimental_well_count = models.IntegerField(null=True, blank=True)
    is_pool = models.NullBooleanField(null=True, blank=True)
    created_by = models.ForeignKey('ScreensaverUser', null=True, blank=True)
    owner_screener = models.ForeignKey('ScreeningRoomUser', null=True, blank=True)
    solvent = models.TextField()
    date_loaded = models.DateTimeField(null=True, blank=True)
    date_publicly_available = models.DateTimeField(null=True, blank=True)
    
    version_number = models.IntegerField(default=0)
    loaded_by = models.ForeignKey('ScreensaverUser',
                                  related_name='libraries_loaded',
                                  null=True, blank=True)
    class Meta:
        db_table = 'library'
    def __unicode__(self):
        return unicode(str((self.short_name, self.start_plate, self.end_plate))) 

class LibraryContentsVersion(models.Model):
    library_contents_version_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    version_number = models.IntegerField()
    library = models.ForeignKey(Library)
    library_contents_loading_activity = models.ForeignKey(
        AdministrativeActivity, related_name='lcv_load')
    library_contents_release_activity = models.ForeignKey(AdministrativeActivity, null=True, blank=True, related_name='lcv_release')
    class Meta:
        db_table = 'library_contents_version'

class Copy(models.Model):
    version = models.IntegerField()
    usage_type = models.TextField()
    library = models.ForeignKey('Library')
    name = models.TextField()
    copy_id = models.IntegerField(primary_key=True)
    comments = models.TextField(blank=True)
    date_created = models.DateTimeField()
    created_by = models.ForeignKey('ScreensaverUser', null=True, blank=True)
    date_plated = models.DateField(null=True, blank=True)
    primary_plate_status = models.TextField()
    primary_plate_location_id = models.IntegerField(null=True, blank=True)
    plates_available = models.IntegerField(null=True, blank=True)
    plate_locations_count = models.IntegerField(null=True, blank=True)
    primary_well_mg_ml_concentration = models.DecimalField(null=True, max_digits=5, decimal_places=3, blank=True)
    primary_well_molar_concentration = models.DecimalField(null=True, max_digits=13, decimal_places=12, blank=True)
    min_molar_concentration = models.DecimalField(null=True, max_digits=13, decimal_places=12, blank=True)
    max_molar_concentration = models.DecimalField(null=True, max_digits=13, decimal_places=12, blank=True)
    min_mg_ml_concentration = models.DecimalField(null=True, max_digits=5, decimal_places=3, blank=True)
    max_mg_ml_concentration = models.DecimalField(null=True, max_digits=5, decimal_places=3, blank=True)
    well_concentration_dilution_factor = models.DecimalField(null=True, max_digits=8, decimal_places=2, blank=True)
    date_loaded = models.DateTimeField(null=True, blank=True)
    date_publicly_available = models.DateTimeField(null=True, blank=True)
    class Meta:
        db_table = 'copy'

class Plate(models.Model):
    plate_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    plate_type = models.TextField(blank=True)
    plate_number = models.IntegerField()
    
#     well_volume = models.DecimalField(null=True, max_digits=10, decimal_places=9, blank=True)
    well_volume = models.FloatField(null=True, blank=True)

    # TODO: decide how to handle library screening plates:
    # - use only remaining volume, set all volumes the same, or 
    # eliminate remaining, and set min/max/avg to the same, or
    # can we update the queries to be efficient enough to not need min/max/avg?
    remaining_volume = models.FloatField(null=True, blank=True)
    avg_remaining_volume = models.FloatField(null=True, blank=True)
    min_remaining_volume = models.FloatField(null=True, blank=True)
    max_remaining_volume = models.FloatField(null=True, blank=True)
    
    screening_count = models.IntegerField(null=True, blank=True)
    
    copy = models.ForeignKey(Copy)
    facility_id = models.TextField(blank=True)
    date_created = models.DateTimeField()
    created_by = models.ForeignKey('ScreensaverUser', null=True, blank=True)
    plate_location = models.ForeignKey('PlateLocation', null=True, blank=True)
    status = models.TextField()
    retired_activity_id = models.IntegerField(unique=True, null=True, blank=True)
    plated_activity_id = models.IntegerField(unique=True, null=True, blank=True)
    stock_plate_number = models.IntegerField(null=True, blank=True)
    quadrant = models.IntegerField(null=True, blank=True)
    min_molar_concentration = models.DecimalField(null=True, max_digits=13, decimal_places=12, blank=True)
    max_molar_concentration = models.DecimalField(null=True, max_digits=13, decimal_places=12, blank=True)
    min_mg_ml_concentration = models.DecimalField(null=True, max_digits=5, decimal_places=3, blank=True)
    max_mg_ml_concentration = models.DecimalField(null=True, max_digits=5, decimal_places=3, blank=True)
    primary_well_molar_concentration = models.DecimalField(null=True, max_digits=13, decimal_places=12, blank=True)
    primary_well_mg_ml_concentration = models.DecimalField(null=True, max_digits=5, decimal_places=3, blank=True)
    date_loaded = models.DateTimeField(null=True, blank=True)
    date_publicly_available = models.DateTimeField(null=True, blank=True)
    class Meta:
        db_table = 'plate'


class CopyWell(models.Model):
#     library = models.ForeignKey('Library')
    plate = models.ForeignKey('Plate')
    copy = models.ForeignKey('Copy')
    # FIXME: name should be "well" - also fix db.apy then
    well = models.ForeignKey('Well')
    plate_number = models.IntegerField()
    volume = models.FloatField(null=True, blank=True)
    initial_volume = models.FloatField(null=True, blank=True)
    adjustments = models.IntegerField()
 
    class Meta:
        db_table = 'copy_well'


class PlateLocation(models.Model):
    plate_location_id = models.IntegerField(primary_key=True)
    bin = models.TextField()
    freezer = models.TextField()
    room = models.TextField()
    shelf = models.TextField()
    class Meta:
        db_table = 'plate_location'


class WellVolumeAdjustment(models.Model):
    well_volume_adjustment_id = models.IntegerField(primary_key=True)
    version = models.IntegerField()
    well = models.ForeignKey(Well)
    lab_cherry_pick = models.ForeignKey('LabCherryPick', null=True, blank=True)
    well_volume_correction_activity = models.ForeignKey('WellVolumeCorrectionActivity', null=True, blank=True)
    volume = models.DecimalField(null=True, max_digits=10, decimal_places=9, blank=True)
    copy = models.ForeignKey(Copy)
    class Meta:
        db_table = 'well_volume_adjustment'

class WellVolumeCorrectionActivity(models.Model):
    activity = models.ForeignKey(AdministrativeActivity, primary_key=True)
    class Meta:
        db_table = 'well_volume_correction_activity'


# this is a LINCS table
# class Cell(models.Model):
#     cell_id = models.IntegerField(primary_key=True)
#     alternate_id = models.CharField(max_length=255, blank=True)
#     alternate_name = models.CharField(max_length=255, blank=True)
#     batch_id = models.CharField(max_length=255, blank=True)
#     cell_type = models.CharField(max_length=255, blank=True)
#     cell_type_detail = models.TextField(blank=True)
#     center_name = models.CharField(max_length=255, blank=True)
#     center_specific_id = models.CharField(max_length=255, blank=True)
#     clo_id = models.CharField(max_length=255, blank=True)
#     disease = models.CharField(max_length=255, blank=True)
#     disease_detail = models.TextField(blank=True)
#     facility_id = models.CharField(max_length=255, unique=True)
#     genetic_modification = models.CharField(max_length=255, blank=True)
#     mutations_explicit = models.TextField(blank=True)
#     mutations_reference = models.TextField(blank=True)
#     name = models.CharField(max_length=255, blank=True)
#     organ = models.CharField(max_length=255, blank=True)
#     organism = models.CharField(max_length=255, blank=True)
#     organism_gender = models.CharField(max_length=255, blank=True)
#     recommended_culture_conditions = models.TextField(blank=True)
#     tissue = models.CharField(max_length=255, blank=True)
#     vendor = models.CharField(max_length=255, blank=True)
#     vendor_catalog_id = models.CharField(max_length=255, blank=True)
#     verification = models.TextField(blank=True)
#     verification_reference_profile = models.TextField(blank=True)
#     date_created = models.DateTimeField()
#     date_loaded = models.DateTimeField(null=True, blank=True)
#     date_publicly_available = models.DateTimeField(null=True, blank=True)
#     created_by = models.ForeignKey('ScreensaverUser', null=True, blank=True)
#     class Meta:
#         db_table = 'cell'
#
# class CellGrowthProperties(models.Model):
#     cell = models.ForeignKey(Cell)
#     growth_property = models.TextField()
#     class Meta:
#         db_table = 'cell_growth_properties'
# 
# class CellLineage(models.Model):
#     cell = models.ForeignKey(Cell, primary_key=True)
#     class Meta:
#         db_table = 'cell_lineage'
# 
# class CellMarkers(models.Model):
#     cell = models.ForeignKey('PrimaryCell')
#     cell_markers = models.TextField()
#     class Meta:
#         db_table = 'cell_markers'
# 
# class CellRelatedProjects(models.Model):
#     cell = models.ForeignKey(Cell)
#     related_project = models.TextField()
#     class Meta:
#         db_table = 'cell_related_projects'
# 
# class PrimaryCell(models.Model):
#     age_in_years = models.IntegerField()
#     donor_ethnicity = models.CharField(max_length=255, blank=True)
#     donor_health_status = models.CharField(max_length=255, blank=True)
#     passage_number = models.IntegerField()
#     cell = models.ForeignKey(Cell, primary_key=True)
#     class Meta:
#         db_table = 'primary_cell'
# LINCS table
# class ExperimentalCellInformation(models.Model):
#     experimental_cell_information_id = models.IntegerField(primary_key=True)
#     cell = models.ForeignKey(Cell)
#     screen = models.ForeignKey('Screen')
#     class Meta:
#         db_table = 'experimental_cell_information'
# 
# class CellUpdateActivity(models.Model):
#     cell = models.ForeignKey('Cell')
#     update_activity = models.ForeignKey(AdministrativeActivity, unique=True)
#     class Meta:
#         db_table = 'cell_update_activity'

