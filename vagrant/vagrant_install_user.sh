#!/bin/bash
set -e

reset='\033[00m'
green='\033[01;32m'
red='\033[01;31m'
yellow='\033[01;33m'
blue='\e[0;34m'

sudo chsh ${linuxuser} -s /usr/bin/zsh
sudo su -c "$(wget https://raw.githubusercontent.com/robbyrussell/oh-my-zsh/master/tools/install.sh -O -)" - ${linuxuser}
ssh-keygen -t rsa -f ~/.ssh/id_rsa -P ''
cat ~/.ssh/id_rsa.pub

# echo "${green}\n-----\n-----\nYou can add this key to bitbucket\n-----\n-----\n${reset}"

cd /home/${linuxuser}
cd agora

$pip install -r requirements.txt
$pip install psycopg2

wget https://raw.githubusercontent.com/nginx/nginx/master/conf/uwsgi_params -O uwsgi_params
sudo mv uwsgi_params /etc/nginx/
sudo mkdir -p /var/${linuxuser}/log /var/${linuxuser}/deploy /var/${linuxuser}/backup /var/${linuxuser}/uploads /var/${linuxuser}/static

# The group `www-data` is supposed to be the group that runs the Nginx process
# For example, in CentOS, this group was simply called `nginx`
sudo usermod -aG www-data ${linuxuser}
sudo chown -R ${linuxuser}:www-data /var/${linuxuser}/

# We don't currently have backups
# crontab -l | { cat; echo "0 0 * * * /home/${linuxuser}/${linuxuser}/daily_simple_backup.sh"; } | crontab -
crontab -l | { cat; echo "*/5 * * * * python3 /home/mta/agora/manage.py runcrons >> /var/mta/log/cronjob.log"; } | crontab -

sudo mkdir -p /etc/uwsgi
sudo ln -s `pwd`/uwsgi.ini /etc/uwsgi/
sudo chown -R ${linuxuser}:www-data /etc/uwsgi/
# sudo openssl dhparam -out /etc/ssl/dhparam.pem 2048

sed -i "s/mta.students.cs.ubc.ca/${server_address}/g" nginx.conf
sed -i "s/%IP%/${server_address}/g" agora/development_settings.py
sed -i "s/%PASS%/${dbpass}/g" agora/development_settings.py
sed -i "s/#PROD //g" agora/development_settings.py

$python manage.py collectstatic --noinput
sudo chown -R ${linuxuser}:www-data /var/${linuxuser}/

sudo ln -s `pwd`/nginx.conf /etc/nginx/conf.d/${linuxuser}_nginx.conf
#sudo ln -s `pwd`/uwsgi.service /etc/systemd/system/${linuxuser}_uwsgi.service
sudo cp uwsgi.service /etc/systemd/system/${linuxuser}_uwsgi.service
sudo systemctl daemon-reload

$python manage.py migrate

# get data from old server or just from your computer
# ./get_data.sh

sudo systemctl restart ${linuxuser}_uwsgi
sudo systemctl restart nginx

sudo systemctl enable ${linuxuser}_uwsgi
sudo systemctl enable nginx

rm -r /home/${linuxuser}/agora/vagrant

# You add the output of the next line to agora/settings.py if need be

# useful commands:
# alter table db_word drop constraint db_word_pkey cascade;
# GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO jerry;

# run server
# python3 manage.py migrate
# python3 manage.py createsuperuser --user ${linuxuser} --email ''  # provide password
# python3 manage.py runserver 0.0.0.0:8000

# TODO: In case you want HTTPS
# sudo apt install -y certbot python-certbot-nginx
# 
# sudo certbot certonly --agree-tos --authenticator standalone --installer nginx \
#     -m farzad.abdolhosseini@gmail.com \  # You might want to change this!
#     --preferred-challenges tls-sni \
#     --pre-hook "systemctl stop nginx" --post-hook "systemctl start nginx" \
#     -d ${server_address}
# 
# sudo crontab -l | { cat; echo "30 4 * * * certbot renew --pre-hook 'systemctl stop nginx' --post-hook 'systemctl start nginx'"; } | sudo crontab -
