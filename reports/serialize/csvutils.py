from __future__ import unicode_literals

import csv
import logging

from reports.serialize import to_simple
from django.utils.encoding import smart_text, force_text


logger = logging.getLogger(__name__)

LIST_DELIMITER_CSV = ';'

def from_csv(csvfile, list_delimiter=LIST_DELIMITER_CSV, list_keys=None):
    '''
    Returns an in memory matrix (array of arrays) for the input file
    
    @param list_keys overrides nested list eval for column keys; no brackets '[]' are 
        needed to denote these columns as list columns - however, to comply with 
        the csv standard, they still have to be quoted (if list_delimiter=csv_delimiter)
    NOTES: 
    - nested lists are denoted by brackets, i.e. '[]',
    - to escape use '\[...' (i.e. when embedding a regex expression)
    TODO: version 2 - read from a stream
    '''
    reader = csv.reader(csvfile)
    return from_csv_iterate(reader, list_delimiter=list_delimiter, list_keys=list_keys)
    
def csv_generator(iterable, list_delimiter=LIST_DELIMITER_CSV, list_keys=None):
    list_keys = list_keys or []
    list_keys = list(list_keys)
    i = 0 
    for row in iterable:
        if i == 0:
            keys = [x for x in row]
        else:
            item = dict(zip(keys,row))
            for key in item.keys():
                val = item[key]
                if val and len(val)> 1:
                    if val[0] == '\\' and val[1] == '[':
                        # this could denote an escaped bracket, i.e. for a regex
                        item[key] = val[1:]
                    elif key in list_keys or val[0]=='[':
                        # due to the simplicity of the serializer, above, any 
                        # quoted string is a nested list
                        list_keys.append(key)
                        item[key] = [
                            x.strip() 
                            for x in val.strip('"[]').split(list_delimiter)]
            yield item
        i += 1
    logger.debug('read in data, count: %d', i )   
    
def from_csv_iterate(iterable, list_delimiter=LIST_DELIMITER_CSV, list_keys=None):
    '''
    Returns an in memory array of dicts for the iterable, representing a 
    csv-like input matrix.
    - the first row is interpreted as the dict keys, unless a list_keys param is 
    specified 
    '''
    list_keys = list_keys or []
    data_result = []
    i = 0
    keys = []
    list_keys = list(list_keys) 
    logger.debug('list_keys: %r', list_keys)
    for row in iterable:
        if i == 0:
            keys = [x for x in row]
        else:
            item = dict(zip(keys,row))
            for key in item.keys():
                val = item[key]
                if val and len(val)> 1:
                    if val[0] == '\\' and val[1] == '[':
                        # this could denote an escaped bracket, i.e. for a regex
                        item[key] = val[1:]
                    elif key in list_keys or val[0]=='[':
                        # due to the simplicity of the serializer, above, any 
                        # quoted string is a nested list
                        list_keys.append(key)
                        item[key] = []
                        for x in val.strip('"[]').split(list_delimiter):
                            x = x.strip()
                            if x:
                                item[key].append(x)
            data_result.append(item)
        i += 1
    logger.debug('read in data, count: ' + str(len(data_result)) )   
    return data_result

def string_convert(val):
    return csv_convert(val, delimiter=',')

def dict_to_rows(_dict):
    ''' Utility that converts a dict into a table for writing to a spreadsheet
    '''
    
    logger.debug('_dict: %r', _dict)
    values = []
    if isinstance(_dict, dict):
        for key,val in _dict.items():
            for row in dict_to_rows(val):
                if not row:
                    values.append([key,None])
                else:
                    keyrow = [key]
                    if isinstance(row, basestring):
                        keyrow.append(row)
                    else:
                        keyrow.extend(row)
                    values.append(keyrow)
    else:
        values = (csv_convert(_dict),)
    return values

def csv_convert(val, delimiter=LIST_DELIMITER_CSV, list_brackets='[]'):
    delimiter = delimiter + ' '
    if isinstance(val, (list,tuple)):
        if list_brackets:
            return ( list_brackets[0] 
                + delimiter.join([smart_text(to_simple(x)) for x in val]) 
                + list_brackets[1] )
        else: 
            return delimiter.join([smart_text(to_simple(x)) for x in val]) 
    elif val != None:
        if type(val) == bool:
            if val:
                return 'TRUE'
            else:
                return 'FALSE'
        else:
            return force_text(to_simple(val))
    else:
        return None
