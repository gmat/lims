from __future__ import unicode_literals

from django.conf.urls import patterns, url, include
from tastypie.api import Api

from db import views
from db.api import ScreensaverUserResource, ScreenResource, \
    ScreenSummaryResource, ScreenResultResource, LabHeadResource, \
    LabAffiliationResource, ScreeningRoomUserResource, DataColumnResource, LibraryResource, \
    LibraryCopyResource, LibraryCopyPlateResource, PlateLocationResource, \
    WellResource, ActivityResource, LibraryContentsVersionResource, ReagentResource, \
    SmallMoleculeReagentResource, SilencingReagentResource, NaturalProductReagentResource, \
    CopyWellResource, UserChecklistItemResource, \
    CopyWellHistoryResource, CherryPickRequestResource, CherryPickPlateResource, \
    AttachedFileResource, ServiceActivityResource, ScreeningRoomUserResource


v1_api = Api(api_name='v1')
v1_api.register(ScreensaverUserResource())
v1_api.register(ScreeningRoomUserResource())
v1_api.register(ScreenResource())
v1_api.register(ScreenResultResource())
v1_api.register(ScreenSummaryResource())
v1_api.register(LabHeadResource())
v1_api.register(LabAffiliationResource())
v1_api.register(ScreeningRoomUserResource())
v1_api.register(DataColumnResource())
v1_api.register(LibraryResource())
v1_api.register(LibraryCopyResource())
v1_api.register(LibraryCopyPlateResource())
v1_api.register(PlateLocationResource())
v1_api.register(WellResource())
v1_api.register(ActivityResource())
v1_api.register(ServiceActivityResource())
v1_api.register(LibraryContentsVersionResource())
v1_api.register(ReagentResource())
v1_api.register(CopyWellHistoryResource())
v1_api.register(CherryPickRequestResource())
v1_api.register(CherryPickPlateResource())
v1_api.register(UserChecklistItemResource())
v1_api.register(AttachedFileResource())
v1_api.register(SmallMoleculeReagentResource())
v1_api.register(SilencingReagentResource())
v1_api.register(NaturalProductReagentResource())
# v1_api.register(LibraryCopyPlatesResource())
v1_api.register(CopyWellResource())

urlpatterns = patterns('',
    url(r'^$', views.main, name="home"),
    url(r'^smiles_image/(?P<well_id>\S+)$','db.views.smiles_image', name="smiles_image" ),
    url(r'^well_image/(?P<well_id>\S+)$','db.views.well_image', name="well_image" ),
    url(r'^attachedfile/content/(?P<attached_file_id>\d+)$',
        'db.views.attached_file', name="attached_file" ),
    (r'^api/', include(v1_api.urls)),
)