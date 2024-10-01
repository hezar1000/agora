#!/bin/bash
# Not every step in this file is always needed + some other steps might be needed from time to time
git pull
python3 manage.py migrate
python3 manage.py collectstatic --no-input
sudo systemctl restart nginx
sudo systemctl restart mta_uwsgi
