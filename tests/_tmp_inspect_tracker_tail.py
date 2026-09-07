from __future__ import annotations
import json
from urllib.parse import quote
from runtime.outscraper_sheet_runner import rclone_access_token,sheets_request,SPREADSHEET_ID

def test_tail():
    token=rclone_access_token(); rng=quote('Sent Email Tracker!A4995:P5015',safe='!:')
    body=sheets_request(token,'GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{rng}?valueRenderOption=FORMATTED_VALUE')
    print(json.dumps(body,ensure_ascii=False,indent=2))
    rng2=quote('Sent Email Tracker!A5865:P5880',safe='!:')
    body2=sheets_request(token,'GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{rng2}?valueRenderOption=FORMATTED_VALUE')
    print('TAIL',json.dumps(body2,ensure_ascii=False,indent=2))
