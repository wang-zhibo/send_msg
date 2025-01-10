#!/usr/bin/env python
# -*- coding:utf-8 -*-

# Author:
# E-mail:
# Date  : 25-01-10
# Desc  :


from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator
from typing import List, Dict
from urllib.parse import unquote
import json
from common.log import logger
import threading
import os

# FastAPI app initialization
app = FastAPI()

# Define data model for validation
class DataItem(BaseModel):
    message: str

    @validator('message')
    def decode_message(cls, v):
        try:
            return unquote(v)  # Decode the message if it's URL-encoded
        except Exception as e:
            logger.warning(f"解码 message 失败: {str(e)}, 使用原始值: {v}")
            return v  # If decoding fails, return the original value

class RequestData(BaseModel):
    data_list: List[DataItem]

    @validator('data_list')
    def validate_data_list(cls, v):
        if not v:
            raise ValueError('data_list不能为空')
        for item in v:
            if not isinstance(item, dict):
                raise ValueError('data_list的每个元素必须为字典类型')
            if 'message' not in item:
                raise ValueError('每个消息必须包含message')
        return v


# POST route to handle send_message requests
@app.post("/send_message")
async def send_message(request_data: RequestData):
    try:
        data_list = request_data.data_list

        # Validate the data (this is now handled by Pydantic validators)
        try:
            # Proceed to save data after validation
            curdir = os.path.dirname(__file__)
            config_path = os.path.join(curdir, "data.json")
            with open(config_path, 'w', encoding='utf-8') as file:
                json.dump([item.dict() for item in data_list], file, ensure_ascii=False)
            logger.info(f"写入成功,写入内容{data_list}")
            return {"status": "success", "message": "发送成功"}
        except Exception as e:
            logger.error(f"写入文件时发生错误: {str(e)}")
            raise HTTPException(status_code=500, detail="服务器内部错误")

    except ValueError as e:
        logger.error(f"数据验证失败: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.error(f"处理请求时发生错误: {str(e)}")
        raise HTTPException(status_code=500, detail="服务器内部错误")


# FileWriter class to run the FastAPI app in a separate thread
class FileWriter:
    def __init__(self):
        super().__init__()
        self.flask_thread = threading.Thread(target=self.run_fastapi_app)
        self.flask_thread.start()

    def run_fastapi_app(self):
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=5688)


