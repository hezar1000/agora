import json
from channels.generic.websocket import AsyncWebsocketConsumer
from urllib import parse

import logging

eventLogger = logging.getLogger("agora.events")

class WebsocketConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        query_string = self.scope.get("query_string").decode("utf-8")
        query_params = parse.parse_qs(query_string)
        self.auth_id = query_params.get("auth_id", [None])[0]
        self.course_id = self.scope['url_route']['kwargs']['course_id']

    async def connect(self):
        self.room_group_name = f'course_{self.course_id}'

        if self.auth_id:
            await self.channel_layer.group_add(
                self.room_group_name,
                self.channel_name
            )

            await self.accept()

            eventLogger.info(f'[{self.auth_id}] connected to course [{self.room_group_name}]')
        else:
            await self.close()

    async def disconnect(self, close_code):
        if self.auth_id:
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )
            
            eventLogger.info(f'[{self.auth_id}] disconnected from course [{self.room_group_name}]')

        self.close()

    async def receive(self, text_data):
        pass

    async def send_message(self, e):
        try:
            eventLogger.info(f'[{self.auth_id}] sending message to course [{self.room_group_name}]; Key: {e["key"]}, Value: {e.get("value", None)}')
            
            if int(e['send_auth_id']) != int(self.auth_id):
                await self.send(text_data=json.dumps({
                    'key': e['key'],
                    'value': e.get('value', None)
                }))

        except Exception as e:
            eventLogger.error(f'[{self.auth_id}] failed to send message to course [{self.room_group_name}]')
            await self.close()