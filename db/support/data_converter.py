from __future__ import unicode_literals
import re
# MIGRATE "OLD" SS1 fields to new fields

# def convert_well_data(
#         data,
#         value_converters={
#             'library_well_type': lambda x: default_converter(x) },
#         field_mapping = {
#             'plate': 'plate_number',
#             'well': 'well_name',
#             'well_type': 'library_well_type',
#             'facility_reagent_id': 'facility_id',
#             'vendor':'vendor_name',
#             'vendor_reagent_id': 'vendor_identifier',
#             'chemical_name': 'compound_name'
#         } ):
#     '''
#     MIGRATE "OLD" SS1 fields to new fields
#     @param data dict of data, converted in place
#     '''
#     new_data = dict(zip(default_converter(key), value) for key,value in data.items())
#     
#     for oldname,newname in field_mapping.items():
#         if newname not in new_data:
#             val_old = next(
#                 (val for (key,val) in data.items() if key.lower()==oldname),None)
#             if val_old: 
#                 new_data[newname] = val_old
#                 if newname in value_converters:
#                     new_data[newname] = value_converters[newname](data[newname]
#                 del new_data[oldname]
#     return new_data

DEFAULT_CONVERTER=re.compile(r'[\W]+')
def default_converter(original_text, sep='_'):
    if not original_text:
        return None
    temp = DEFAULT_CONVERTER.sub(' ', original_text)
    return sep.join(temp.lower().split())      
  