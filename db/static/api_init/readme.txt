The  api_init files are used to "bootstrap" the database with meta information
that defines the various Resources available through the API.

IMPORTANT: the reports/static/api_init/api_init_actions.csv file must be run 
before the files in this directory can be processed.

- The metahash_fields_resource.csv file defines what a "Resource" record looks
like.
- metahash_resource.data.csv defines actual instances of Resources.

- All of the "fields" files define the fields in the various other resources
that are available through the API.

- api_init_actions.csv is a recipe file used by the "db_init" script, telling
it what order to load each of the api_init files in the bootstrapping process. 
It is run like this:
run the server in a localhost port:
(virtualenv)$./manage.py runserver 55001
run the bootloader script:

(virtualenv) <project_root_dir>$ PYTHONPATH=. python reports/utils/db_init.py  \
  --input_dir=./db/static/api_init/ \
  -f ./db/static/api_init/api_init_actions.csv \
  -u http://localhost:8000/reports/api/v1 -U <user>
  
  
(virtualenv) <project_root_dir>$ PYTHONPATH=. python reports/utils/db_init.py  \
  --input_dir=./reports/static/api_init/ \
  -f ./reports/static/api_init/api_init_actions.csv \
  -u http://localhost:8000/reports/api/v1 -U <user>


(OLD): using manage.py script:
(virtualenv)$ ./manage.py db_init  --inputDirectory=./reports/static/api_init/ \
  -f ./reports/static/api_init/api_init_actions.csv \
  -u http://localhost:55001/reports/api/v1 -a <user:password>
