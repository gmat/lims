# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import csv
import logging
import os

from django.db import migrations, models

from lims.base_settings import PROJECT_ROOT
from reports.utils.gray_codes import create_substance_id
from db.models import create_id

logger = logging.getLogger(__name__)

class Migration(migrations.Migration):

    dependencies = [
        ('db', '0020_current_work'),
    ]

    operations = [
        migrations.AlterField(
            model_name='attachedfile',
            name='type',
            field=models.TextField()),
        migrations.RemoveField(
            model_name='attachedfile',name='attached_file_type_id'),
        migrations.DeleteModel('ScreenFundingSupportLink'),

#         migrations.DeleteModel('ScreeningRoomUser'),
#         migrations.DeleteModel('LabHead'),
    ]