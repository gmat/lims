# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import logging

from django.db import migrations, models

import db.models
import django.db.models.deletion


logger = logging.getLogger(__name__)

def _update_table_autofield(db, table, column):
    
    ###
    # Converting to Autofields in Django
    # Creating primary key "AutoFields" for the old java/hibernate 
    # "GenericGenerator" class
    #
    # example:
    # Changing field 'Screen.screen_id' to auto field
    # *NOTE: the following does not work with Postgres, for an already 
    # existing field:
    # db.alter_column(u'screen', 'screen_id', self.gf('django.db.models.fields.AutoField')(primary_key=True))
    # ( Postgres can create a field of type 'serial', but it is not a real type, 
    # so postgres will not convert a field to 'serial'; as would be needed to work
    # with the Django Autofield;
    # see: http://www.postgresql.org/docs/8.3/interactive/datatype-numeric.html#DATATYPE-SERIAL
    # see: http://south.aeracode.org/ticket/407, 
    # Fix is as follows:
    
    # Note: we don't need to create the sequence; just re-associate the old one
    # Note - 20140826:
    # -- moved out of 0012_create_reagent_autofield
    # because the migration bootstrap step needs these fields to be here.
    # Also: note: we have not modified the "models" section below, because some 
    # we would also have to change all the other migrations for consistency;
    # we'll just keep the very last schema migration up-to-date.
    
    # Note: we don't need to create the sequence; just re-associate the old one

    # first change the name to use the standard django naming
    db.execute(
        "ALTER SEQUENCE {column}_seq RENAME to {table}_{column}_seq".format(
            table=table, column=column))
    db.execute(
        ( "ALTER TABLE {table} ALTER COLUMN {column} "
          "SET DEFAULT nextval('{table}_{column}_seq'::regclass)").format(
              table=table, column=column))
    db.execute(
        "ALTER SEQUENCE {table}_{column}_seq OWNED BY {table}.{column}".format(
            table=table, column=column))

def convert_django_autofields(apps, schema_editor):
    
    table_autofields = (
        ('reagent', 'reagent_id'),
        ('screen', 'screen_id'),
        ('library', 'library_id'),
        ('gene', 'gene_id'),
        ('screensaver_user', 'screensaver_user_id'),
        ('attached_file', 'attached_file_id'),
        ('activity', 'activity_id'),
        ('equipment_used','equipment_used_id'),
        ('annotation_type','annotation_type_id'),
        ('assay_plate','assay_plate_id'),
        ('assay_well','assay_well_id'),
        ('cherry_pick_request','cherry_pick_request_id'),
        ('lab_cherry_pick','lab_cherry_pick_id'),
        ('cherry_pick_assay_plate','cherry_pick_assay_plate_id'),
        ('lab_affiliation','lab_affiliation_id'),
        ('publication','publication_id'),
        ('screen_result','screen_result_id'),
        ('data_column','data_column_id'),
        ('screener_cherry_pick','screener_cherry_pick_id'),
        ('copy','copy_id'),
        ('plate','plate_id'),
        ('plate_location','plate_location_id'),
        ('well_volume_adjustment','well_volume_adjustment_id'),
        )
    for (table, key_field) in table_autofields:
        logger.info(str(('_update_table_autofield', table, key_field)))
        _update_table_autofield(schema_editor,table, key_field)

def alter_attached_file_to_screensaver_user(apps, schema_editor):
    db = schema_editor
    
    sub_table = 'attached_file'
    fk_column = 'screensaver_user_id'
    new_parent = 'screensaver_user'
    new_parent_column = 'screensaver_user_id'
    
    logger.info(str(('alter foreign key', sub_table, fk_column, new_parent, new_parent_column)))
    db.execute(
        ( "ALTER TABLE {table} RENAME COLUMN {column} to tmp_{column}").format(
              table=sub_table, column=fk_column))
    db.execute(
        ( "ALTER TABLE {table} ADD COLUMN {column} integer").format(
              table=sub_table, column=fk_column))
    db.execute(
        ( "update {table} set {column} = tmp_{column}").format(
              table=sub_table, column=fk_column))
    db.execute(
        ("ALTER TABLE {table} ADD CONSTRAINT fk_{column} "
            "FOREIGN KEY ({column}) "
            "REFERENCES {other_table} ({other_column}) ").format(
                table=sub_table, column=fk_column, 
                other_table=new_parent, other_column=new_parent_column))
    db.execute(
        ( "ALTER TABLE {table} DROP COLUMN tmp_{column} ").format(
              table=sub_table, column=fk_column))

def _alter_table_parent(db, sub_table, new_primary_key, fk_column, 
                        new_parent, new_parent_column):
    
    # alter table molfile rename column reagent_id to smr_reagent_id;
    # alter table molfile add column reagent_id integer;
    # update molfile set reagent_id = smr_reagent_id;
    # alter table molfile add constraint reagent_fk FOREIGN KEY (reagent_id) 
    #     REFERENCES reagent (reagent_id);
    # alter table molfile alter column reagent_id set NOT NULL;
    # alter table molfile drop column smr_reagent_id;
    # ALTER TABLE small_molecule_compound_name ADD PRIMARY KEY (reagent_id, ordinal);

    ## NOTE: we are copying/deleting/making new foreign key because it is 
    ## proving difficult to find the constraint for the extant foreign key
    logger.info(str(('alter foreign key', sub_table, fk_column, new_parent, new_parent_column)))
    db.execute(
        ( "ALTER TABLE {table} RENAME COLUMN {column} to tmp_{column}").format(
              table=sub_table, column=fk_column))
    db.execute(
        ( "ALTER TABLE {table} ADD COLUMN {column} integer").format(
              table=sub_table, column=fk_column))
    db.execute(
        ( "update {table} set {column} = tmp_{column}").format(
              table=sub_table, column=fk_column))
    db.execute(
        ("ALTER TABLE {table} ADD CONSTRAINT fk_{column} "
            "FOREIGN KEY ({column}) "
            "REFERENCES {other_table} ({other_column}) ").format(
                table=sub_table, column=fk_column, 
                other_table=new_parent, other_column=new_parent_column))
    db.execute(
        ( "ALTER TABLE {table} ALTER COLUMN {column} set NOT NULL").format(
              table=sub_table, column=fk_column))
    db.execute(
        ( "ALTER TABLE {table} DROP COLUMN tmp_{column} ").format(
              table=sub_table, column=fk_column))
    db.execute(
        ( "ALTER TABLE {table} ADD PRIMARY KEY ({primary_key})").format(
              table=sub_table, primary_key=new_primary_key))

def alter_table_parents(apps, schema_editor):
    db = schema_editor
    _alter_table_parent(
        db,'molfile', 'reagent_id','reagent_id', 'reagent', 'reagent_id')
    _alter_table_parent(
        db,'small_molecule_compound_name', 'reagent_id,ordinal', 
        'reagent_id', 'reagent', 'reagent_id')
    _alter_table_parent(
        db,'small_molecule_pubchem_cid', 'reagent_id,pubchem_cid',
        'reagent_id', 'reagent', 'reagent_id')
    _alter_table_parent(
        db,'small_molecule_chembank_id', 'reagent_id,chembank_id',
        'reagent_id', 'reagent', 'reagent_id')
    _alter_table_parent(
        db,'small_molecule_chembl_id', 'reagent_id,chembl_id',
        'reagent_id', 'reagent', 'reagent_id')

def create_reagent_ids(apps, schema_editor):
    
    for reagent in apps.get_model('db','Reagent').objects.all():
        reagent.substance_id = db.models.create_id()
        reagent.save()

class Migration(migrations.Migration):

    dependencies = [
        ('db', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Substance',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, 
                    auto_created=True, primary_key=True)),
                ('comment', models.TextField()),
            ],
            options={
                'db_table': 'substance',
            },
        ),
        migrations.AddField(
            model_name='library',
            name='version_number',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='reagent',
            name='substance_id',
            field=models.CharField(null=True,max_length=8),
        ),
#         migrations.RunPython(create_reagent_ids),
#         migrations.AlterField(
#             model_name='reagent',
#             name='substance_id',
#             field=models.CharField(unique=True, max_length=8),
#             ),
        migrations.AddField(
            model_name='screen',
            name='status_date',
            field=models.DateField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='screen',
            name='status',
            field=models.TextField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name='silencingreagent',
            name='vendor_gene',
            field=models.OneToOneField(
                related_name='vendor_reagent', null=True, blank=True, 
                to='db.Gene', unique=True),
        ),
        migrations.AddField(
            model_name='silencingreagent',
            name='facility_gene',
            field=models.OneToOneField(
                related_name='facility_reagent', null=True, blank=True, 
                to='db.Gene', unique=True),
        ),
        migrations.AddField(
            model_name='library',
            name='loaded_by',
            field=models.ForeignKey(related_name='libraries_loaded', 
                blank=True, to='db.ScreensaverUser', null=True),
        ),
        migrations.RunPython(convert_django_autofields),
         
        migrations.RemoveField(model_name='reagent',name='facility_batch_id'),
        migrations.RemoveField(model_name='smallmoleculereagent',name='salt_form_id'),
         
        migrations.RemoveField(model_name='well',name='version'),
        migrations.RemoveField(model_name='library',name='version'),
        migrations.RemoveField(model_name='screensaveruser',name='version'),
        migrations.RemoveField(model_name='activity',name='version'),
        migrations.RemoveField(model_name='attachedfile',name='version'),
        migrations.RemoveField(model_name='abasetestset',name='version'),
        migrations.RemoveField(model_name='equipmentused',name='version'),
        migrations.RemoveField(model_name='annotationtype',name='version'),
        migrations.RemoveField(model_name='assayplate',name='version'),
        migrations.RemoveField(model_name='assaywell',name='version'),
        migrations.RemoveField(model_name='cellline',name='version'),
        migrations.RemoveField(model_name='checklistitem',name='version'),
        migrations.RemoveField(model_name='cherrypickrequest',name='version'),
        migrations.RemoveField(model_name='labcherrypick',name='version'),
        migrations.RemoveField(model_name='cherrypickassayplate',name='version'),
        migrations.RemoveField(model_name='labaffiliation',name='version'),
        migrations.RemoveField(model_name='publication',name='version'),
        migrations.RemoveField(model_name='screen',name='version'),
        migrations.RemoveField(model_name='screenresult',name='version'),
        migrations.RemoveField(model_name='datacolumn',name='version'),
        migrations.RemoveField(model_name='screenercherrypick',name='version'),
        migrations.RemoveField(model_name='transfectionagent',name='version'),
        migrations.RemoveField(model_name='librarycontentsversion',name='version'),
        migrations.RemoveField(model_name='copy',name='version'),
        migrations.RemoveField(model_name='plate',name='version'),
        migrations.RemoveField(model_name='wellvolumeadjustment',name='version'),
         
        migrations.RunPython(alter_attached_file_to_screensaver_user),
        migrations.RunPython(alter_table_parents),
        migrations.RunSQL(("ALTER TABLE {table} DROP COLUMN {column} ").format(
                  table='molfile', column='ordinal')),
        migrations.RunSQL(("ALTER TABLE {table} ADD CONSTRAINT {table}_{column}_unique UNIQUE({column})").format(
            table='screen_result', column='screen_id')),
        migrations.AddField(
            model_name='screensaveruser',
            name='user',
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.SET_NULL, 
                to='reports.UserProfile', null=True),
        ),
        migrations.AddField(
            model_name='screensaveruser',
            name='username', 
            field=models.TextField(unique=True, null=True)),
        migrations.AlterField(
            model_name='screensaveruser',
            name='login_id',
            field=models.TextField(unique=True, null=True)),
        migrations.AddField(
            model_name='plate',
            name='remaining_volume', 
            field=models.FloatField(null=True, blank=True)),
        migrations.AddField(
            model_name='plate',
            name='avg_remaining_volume', 
            field=models.FloatField(null=True, blank=True)),
        migrations.AddField(
            model_name='plate',
            name='min_remaining_volume', 
            field=models.FloatField(null=True, blank=True)),
        migrations.AddField(
            model_name='plate',
            name='max_remaining_volume', 
            field=models.FloatField(null=True, blank=True)),
        migrations.AddField(
            model_name='plate',
            name='screening_count', 
            field=models.IntegerField(null=True, blank=True)),
        migrations.AddField(
            model_name='cherrypickassayplate',
            name='status', 
            field=models.TextField(null=True)),
        migrations.CreateModel(
            name='CopyWell',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('plate_number', models.IntegerField()),
                ('volume', models.FloatField(null=True, blank=True)),
                ('initial_volume', models.FloatField(null=True, blank=True)),
                ('adjustments', models.IntegerField()),
                ('copy', models.ForeignKey(to='db.Copy')),
            ],
            options={
                'db_table': 'copy_well',
            },
        ),
        migrations.AddField(
            model_name='copywell',
            name='plate',
            field=models.ForeignKey(to='db.Plate'),
        ),
        migrations.AddField(
            model_name='copywell',
            name='well',
            field=models.ForeignKey(to='db.Well'),
        ),
        migrations.CreateModel(
            name='CachedQuery',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('key', models.TextField(unique=True)),
                ('sql', models.TextField()),
                ('uri', models.TextField()),
                ('params', models.TextField(null=True)),
                ('datetime', models.DateTimeField(default=django.utils.timezone.now)),
                ('username', models.CharField(max_length=128)),
                ('count', models.IntegerField(null=True)),
            ],
            options={
                'db_table': 'cached_query',
            },
        ),
        migrations.CreateModel(
            name='UserChecklistItem',
            fields=[
                ('id', models.AutoField(verbose_name='ID', 
                    serialize=False, auto_created=True, primary_key=True)),
                ('item_group', models.TextField()),
                ('item_name', models.TextField()),
                ('status', models.TextField()),
                ('status_date', models.DateField()),
            ],
            options={
                'db_table': 'user_checklist_item',
            },
        ),
        migrations.AddField(
            model_name='userchecklistitem',
            name='admin_user',
            field=models.ForeignKey(related_name='userchecklistitems_created', 
                to='db.ScreensaverUser'),
        ),
        migrations.AddField(
            model_name='userchecklistitem',
            name='screensaver_user',
            field=models.ForeignKey(to='db.ScreensaverUser'),
        ),
        migrations.AlterUniqueTogether(
            name='userchecklistitem',
            unique_together=set([('screensaver_user', 'item_group', 'item_name')]),
        ),
        migrations.AddField(
            model_name='attachedfile',
            name='type',
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name='serviceactivity',
            name='funding_support',
            field=models.TextField(null=True),
        ),
        migrations.CreateModel(
            name='ScreenFundingSupports',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('funding_support', models.TextField()),
                ('screen', models.ForeignKey(to='db.Screen')),
            ],
            options={
                'db_table': 'screen_funding_supports',
            },
        ),
        migrations.AlterUniqueTogether(
            name='screenfundingsupports',
            unique_together=set([('screen', 'funding_support')]),
        ),
               
        
    ]
