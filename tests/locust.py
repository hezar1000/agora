import time
import queue

from locust import HttpUser, task, between, events, main
from socketio import SocketIOUser
from gevent.lock import Semaphore

all_users_spawned = Semaphore()
all_users_spawned.acquire()

INITIAL_WS = "/ws/1/"
max_students = 1000

password = "agoraagora"

user_queue = queue.Queue(maxsize=max_students)
for i in range(1, max_students + 1):
    user_queue.put(i)
    
instructor = "Hedayat"

@events.init.add_listener
def _(environment, **kw):
    @environment.events.spawning_complete.add_listener
    def on_spawning_complete(**kw):
        print("All users spawned")
        all_users_spawned.release()


class User(HttpUser, SocketIOUser):
    host = "https://stg-mta.students.cs.ubc.ca"
    # host = "http://localhost:8000"
    wait_time = between(30, 600)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.csrf_token = ""
        self.is_student = True

        if self.is_student:
            try:
                self.user = user_queue.get_nowait()
            except queue.Empty:
                print("No available users.")
                self.user = None
                self.environment.runner.quit()
        else:
            self.user = instructor
            
    def on_start(self):
        self.login()

    def on_stop(self):
        if self.connected:
            self.disconnect()
            
            print(f"User {self.user} disconnected")

        user_queue.put(self.user)
        print(f"User {self.user} returned to queue")

    def login(self):
        referer = self.client.base_url
        login_page_response = self.client.get("/account/login/")
        self.csrf_token = login_page_response.cookies.get('csrftoken', "")

        login_url = login_page_response.url
        headers = {
            "X-CSRFToken": self.csrf_token,
            "Referer": referer,
            "Origin": self.host,
            "Host": self.host.split("//")[1].split("/")[0],
        }

        login_payload = {
            "stid": str(self.user),
            "password": password,
            "csrfmiddlewaretoken": self.csrf_token,
        }
        response = self.client.post(login_url, data=login_payload, headers=headers)

        if response.status_code == 200:
            print(f"Login successful as {self.user}")
        else:
            print(f"Login failed")
            print(response.status_code, response.reason)

    def connect_sockets(self):
        if not self.user:
            return

        auth_url = f"wss://stg-mta.students.cs.ubc.ca/ws/1/?auth_id={self.user}"
        if not self.connected:
            self.connect(auth_url)

    @task
    def enter_class(self):
        if not self.user:
            return

        self.connect_sockets()

        headers = {
            "X-CSRFToken": self.csrf_token,
            "Referer": self.client.base_url,
            "Origin": self.host,
            "Host": self.host.split("//")[1].split("/")[0],
        }
        response = self.client.get("/course/1/", headers=headers)

        if response.status_code == 200:
            print("Entered class")
        else:
            print("Failed to enter class")
            print(response.status_code, response.reason)
            print(response.text)

class Student(User):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @task
    def raise_lower_hands(self):
        if not self.user:
            return
        
        self.connect_sockets()
        
        all_users_spawned.wait()

        headers = {
            "X-CSRFToken": self.csrf_token,
            "Referer": self.client.base_url,
            "Origin": self.host,
            "Host": self.host.split("//")[1].split("/")[0],
        }
        
        def hands():
            response = self.client.get("/enable", headers=headers)
            print("Hand Raised")
            time.sleep(3)
            
            response = self.client.get("/disable", headers=headers)
            print("Hand Lowered")
            time.sleep(3)

            hands()

        hands()
            
    @task
    def send_poll_answer(self):
        if not self.user:
            return
        
        self.enter_class()
        
        all_users_spawned.wait()

        headers = {
            "X-CSRFToken": self.csrf_token,
            "Referer": self.client.base_url,
            "Origin": self.host,
            "Host": self.host.split("//")[1].split("/")[0],
        }

        data = {
            "csrfmiddlewaretoken": self.csrf_token,
            "poll-answer": "A",
            "poll_id": 1,
        }

        response = self.client.get("/polling/student/", data=data, headers=headers)

        if response:
            print("Sent Poll Result")
            
    @task
    def check_status(self):
        if not self.user:
            return
        
        self.enter_class()
        
        all_users_spawned.wait()
        
        headers = {
            "X-CSRFToken": self.csrf_token,
            "Referer": self.client.base_url,
            "Origin": self.host,
            "Host": self.host.split("//")[1].split("/")[0],
        }
        
        def helper():
            response = self.client.get("/check_status/", headers=headers)
            print("Got Status")
            time.sleep(3)

            helper()
        
        helper()
    
class Instructor(User):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        self.is_student = False
        
    @task
    def pick_student(self):
        if not self.user:
            return
        
        self.connect_sockets()

        headers = {
            "X-CSRFToken": self.csrf_token,
            "Referer": self.client.base_url,
            "Origin": self.host,
            "Host": self.host.split("//")[1].split("/")[0],
        }
        response = self.client.get("/random_student", headers=headers)

        if response.status_code == 200:
            print("Student picked")
        else:
            print("Failed to pick student")
            print(response.status_code, response.reason)
            print(response.text)
            
    @task
    def update_poll_results(self):
        if not self.user:
            return

        self.enter_class()

        headers = {
            "X-CSRFToken": self.csrf_token,
            "Referer": self.client.base_url,
            "Origin": self.host,
            "Host": self.host.split("//")[1].split("/")[0],
        }

        data = {
            "csrfmiddlewaretoken": self.csrf_token,
            "update-results": 1,
        }

        response = self.client.get("/polling/instructor/", data=data, headers=headers)

        if response:
            print("Got Poll Result")
            
    @task
    def count_hands_up(self):
        if not self.user:
            return

        self.enter_class()

        all_users_spawned.wait()
        
        headers = {
            "X-CSRFToken": self.csrf_token,
            "Referer": self.client.base_url,
            "Origin": self.host,
            "Host": self.host.split("//")[1].split("/")[0],
        }

        data = {
            "csrfmiddlewaretoken": self.csrf_token,
        }
        
        def helper():
            response = self.client.get("/count_hands_up/", data=data, headers=headers)
            print("Got Hand Count")
            time.sleep(3)

            helper()

        helper()