import requests

host = "https://stg-mta.students.cs.ubc.ca"
password = "agoraagora"
enrollment_code = "08fc044f"
sessions = {}

start_index = 1001;
max_students = 1100;

def raise_green_hand(session):
    url = host + "/enable/"
    
    page_response = session.get(url)
    csrf_token = page_response.cookies.get('csrftoken', "")
    headers = {
        "X-CSRFToken": csrf_token,
        "Referer": url,
        "Origin": host,
        "Host": "stg-mta.students.cs.ubc.ca"
    }
    
    response = session.post(url, headers=headers)
    if response.status_code == 200:
        # print the response text
        print("Green hand raised")
    else:
        print("Failed to raise green hand")
        print(response.status_code, response.reason)
        print(response.text)

def join_course(session):
    url = host + "/course/enroll/"
    
    page_response = session.get(url)
    csrf_token = page_response.cookies.get('csrftoken', "")
    headers = {
        "X-CSRFToken": csrf_token,
        "Referer": url,
        "Origin": host,
        "Host": "stg-mta.students.cs.ubc.ca"
    }
    
    data = {
        "coursecode": enrollment_code,
        "csrfmiddlewaretoken": csrf_token
    }
    
    response = session.post(url, data=data, headers=headers)
    if response.status_code == 200:
        print("Joined course")
    else:
        print("Failed to join course")
        print(response.status_code, response.reason)
        print(response.text)

def login(stid, password):
    url = host + "/account/login/"
    session = requests.Session()
    
    page_response = session.get(url)
    csrf_token = page_response.cookies.get('csrftoken', "")
    headers = {
        "X-CSRFToken": csrf_token,
        "Referer": url,
        "Origin": host,
        "Host": "stg-mta.students.cs.ubc.ca"
    }

    data = {
        "stid": stid,
        "password": password,
        "csrfmiddlewaretoken": csrf_token
    }

    response = session.post(url, data=data, headers=headers)
    if response.status_code == 200:
        print(f"Logged in as student {stid}")
        return session
    else:
        print(f"Failed to login as student {stid}")
        print(response.status_code, response.reason)
        print(response.text)
        return None
    
def add_student(session, username):
    url = host + "/admin/auth/user/add/"
    
    page_response = session.get(url)
    csrf_token = page_response.cookies.get('csrftoken', "")
    headers = {
        "X-CSRFToken": csrf_token,
        "Referer": url,
        "Origin": host,
        "Host": "stg-mta.students.cs.ubc.ca"
    }
    
    data = {
        "username": username,
        "password1": password,
        "password2": password,
        "_save": True,
        "csrfmiddlewaretoken": csrf_token
    }
    
    response = session.post(url, data=data, headers=headers)
    if response.status_code == 200:
        print(f"Added student {username}")
        return session
    else:
        print(f"Failed to login as student {username}")
        print(response.status_code, response.reason)
        print(response.text)
        return None

if __name__ == "__main__":
    admin_session = login("Hedayat", password)

    for stid in range(start_index, max_students + 1):
        add_student(admin_session, str(stid))
        
        user_session = login(str(stid), password)
        if user_session:
            join_course(user_session)
            sessions[str(stid)] = user_session