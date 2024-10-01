import json
import logging
import re
import time
import rel
import gevent
from locust import User, events
from locust_plugins import missing_extra

try:
    import websocket
except ModuleNotFoundError:
    missing_extra("paho", "mqtt")


class SocketIOUser(User):
    abstract = True
    message_regex = re.compile(r"(\d*)(.*)")
    description_regex = re.compile(r"<([0-9]+)>$")
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ws = None
        
        self.init_ws_time = None
        self.connected = False

    def connect(self, host: str):
        self.init_ws_time = time.time()
        
        try:
            self.ws = websocket.WebSocketApp(host, on_open=self.on_open, on_close=self.on_close, on_error=self.on_error, on_message=self.on_message)
            self.ws.run_forever(dispatcher=rel, reconnect=10)
            rel.signal(2, rel.abort)
            rel.dispatch()
        except Exception as e:
            self.connected = False
            
            events.request.fire(
                request_type="WSS",
                name="Handshake",
                response_time=(time.time() - self.init_ws_time) * 1000,
                response_length=0,
                exception=e,
            )
        
    def on_open(self, ws):
        print("Connected to websocket")
        self.connected = True
        
        events.request.fire(
            request_type="WSS",
            name="Handshake",
            response_time=(time.time() - self.init_ws_time) * 1000,
            response_length=0,
        )

    def on_close(self, ws, close_status_code, close_msg):
        self.ws = None
        self.connected = False
        
    def on_error(self, ws, error):
        self.ws = None
        self.connected = False

        events.request.fire(
            request_type="WSS",
            name="Handshake",
            response_time=(time.time() - self.init_ws_time) * 1000,
            response_length=0,
            exception=error,
        )

    def on_message(self, ws, message):  # override this method in your subclass for custom handling
        m = self.message_regex.match(message)
        response_time = 0  # unknown
        if m is None:
            # uh oh...
            raise Exception(f"got no matches for {self.message_regex} in {message}")
        code = m.group(1)
        json_string = m.group(2)
        if code == "0":
            name = "0 open"
        elif code == "3":
            name = "3 heartbeat"
        elif code == "40":
            name = "40 message ok"
        elif code == "42":
            # this is rather specific to our use case. Some messages contain an originating timestamp,
            # and we use that to calculate the delay & report it as locust response time
            # see it as inspiration rather than something you just pick up and use
            current_timestamp = time.time()
            obj = json.loads(json_string)
            logging.debug(json_string)
            ts_type, payload = obj
            name = f"{code} {ts_type} apiUri: {payload['apiUri']}"

            if payload["value"] != "":
                value = payload["value"]

                if "draw" in value:
                    description = value["draw"]["description"]
                    description_match = self.description_regex.search(description)
                    if description_match:
                        sent_timestamp = int(description_match.group(1))
                        response_time = current_timestamp - sent_timestamp
                    else:
                        # differentiate samples that have no timestamps from ones that do
                        name += "_"
                elif "source_ts" in value:
                    sent_timestamp = value["source_ts"]
                    response_time = (current_timestamp - sent_timestamp) * 1000
            else:
                name += "_missingTimestamp"
        else:
            print(f"Received unexpected message: {message}")
            return
        self.environment.events.request.fire(
            request_type="WSR",
            name=name,
            response_time=response_time,
            response_length=len(message),
            exception=None,
            context=self.context(),
        )

    def send(self, body, name=None, context={}, opcode=websocket.ABNF.OPCODE_TEXT):
        if not name:
            if body == "2":
                name = "2 heartbeat"
            else:
                # hoping this is a subscribe type message, try to detect name
                m = re.search(r'(\d*)\["([a-z]*)"', body)
                assert m is not None
                code = m.group(1)
                action = m.group(2)
                url_part = re.search(r'"url": *"([^"]*)"', body)
                assert url_part is not None
                url = re.sub(r"/[0-9_]*/", "/:id/", url_part.group(1))
                name = f"{code} {action} url: {url}"

        self.environment.events.request.fire(
            request_type="WSS",
            name=name,
            response_time=None,
            response_length=len(body),
            exception=None,
            context={**self.context(), **context},
        )
        logging.debug(f"WSS: {body}")
        self.ws.send(body, opcode)
        
    def disconnect(self):
        self.ws.close()
        self.ws = None
        self.connected = False

    def sleep_with_heartbeat(self, seconds):
        while seconds >= 0:
            gevent.sleep(seconds)
            seconds = 0
            self.send("2")