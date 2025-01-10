#!/usr/bin/env python
# -*- coding:utf-8 -*-

# Author:
# E-mail:
# Date  :
# Desc  :


import os
import json
import requests
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from plugins import Plugin, register
from plugins.send_msg.file_api import FileWriter
from config import conf


# 初始化日志记录器
logger = logging.getLogger(__name__)


class FileChangeHandler(FileSystemEventHandler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith('data.json'):
            self.callback()


@register(
    name="send_msg",
    desire_priority=180,
    hidden=True,
    desc="Watchdog 监听文件变化发送消息 & 微信命令发送消息",
    version="0.0.1",
    author="xdeek"
)
class FileWatcherPlugin(Plugin):
    def __init__(self):
        super().__init__()
        self.channel = None
        self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context

        # 初始化 FileWriter API 服务
        curdir = os.path.dirname(os.path.abspath(__file__))
        FileWriter()

        # 设置文件监视
        self.file_path = os.path.join(curdir, "data.json")
        self.observer = Observer()
        self.event_handler = FileChangeHandler(self.handle_message)
        self.start_watch()

        # 根据配置初始化通信频道
        self.channel_type = conf().get("channel_type", "wx")
        self.initialize_channel()

    def initialize_channel(self):
        if self.channel_type == "wx":
            try:
                import itchat
                self.channel = itchat
            except ImportError as e:
                logger.error(f"未安装 itchat: {e}")
        elif self.channel_type == "ntchat":
            try:
                from channel.wechatnt.ntchat_channel import wechatnt
                self.channel = wechatnt
            except ImportError as e:
                logger.error(f"未安装 ntchat: {e}")
        else:
            logger.error(f"不支持的 channel_type: {self.channel_type}")

    def on_handle_context(self, e_context: EventContext):
        context = e_context.get('context', {})
        if context.type != ContextType.TEXT:
            return

        content = context.content.strip()

        if content == "$start watchdog":
            self.start_watch()
            e_context.action = EventAction.BREAK_PASS
            e_context['reply'] = self.create_reply(ReplyType.INFO, "Watchdog 已启动。")
            self.handle_message()

        elif content == "$stop watchdog":
            self.stop_watch()
            e_context.action = EventAction.BREAK_PASS
            e_context['reply'] = self.create_reply(ReplyType.INFO, "Watchdog 已停止。")

        elif content == "$check watchdog":
            status = "Watchdog 正在运行。如需停止，请使用命令 $stop watchdog。" if self.observer.is_alive() else "Watchdog 未在运行。如需启动，请使用命令 $start watchdog。"
            e_context['reply'] = self.create_reply(ReplyType.INFO, status)
            e_context.action = EventAction.BREAK_PASS

        elif content.startswith("$send_msg"):
            self.handle_send_msg_command(content, e_context)

    def create_reply(self, reply_type, content):
        reply = Reply()
        reply.type = reply_type
        reply.content = content
        return reply

    def handle_send_msg_command(self, content, e_context):
        try:
            receiver_names, message, group_names = self.parse_send_msg_command(content)
            self.send_message(receiver_names, message, group_names)
            e_context['reply'] = self.create_reply(ReplyType.INFO, "消息发送成功。")
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            e_context['reply'] = self.create_reply(ReplyType.ERROR, f"消息发送失败: {str(e)}")
        e_context.action = EventAction.BREAK_PASS

    def parse_send_msg_command(self, command):
        """
        解析 $send_msg 命令。
        预期格式：
            $send_msg [名称1, 名称2] 消息内容
            $send_msg [名称1, 名称2] 消息内容 group[群聊1, 群聊2]
        """
        import re

        pattern = r'^\$send_msg\s*(\[[^\]]*\])?\s*(.*?)\s*(group\[[^\]]*\])?$'
        match = re.match(pattern, command)
        if not match:
            raise ValueError("命令格式不正确。")

        receiver_part, message, group_part = match.groups()

        receiver_names = [name.strip() for name in receiver_part.strip('[]').split(',') if name.strip()] if receiver_part else []
        group_names = [name.strip() for name in group_part.strip('group[]').split(',') if name.strip()] if group_part else []

        return receiver_names, message, group_names

    def start_watch(self):
        if not self.observer.is_alive():
            self.observer.schedule(self.event_handler, path=os.path.dirname(self.file_path), recursive=False)
            self.observer.start()
            logger.info("Watchdog 已启动。")
        else:
            logger.info("Watchdog 已在运行。")

    def stop_watch(self):
        if self.observer.is_alive():
            self.observer.stop()
            self.observer.join()
            logger.info("Watchdog 已停止。")
        else:
            logger.info("Watchdog 未在运行。")

    def handle_message(self):
        try:
            if not os.path.exists(self.file_path):
                logger.warning(f"文件 {self.file_path} 不存在。")
                return

            with open(self.file_path, 'r', encoding='utf-8') as file:
                data = file.read().strip()

            if data:
                data_list = json.loads(data)
                if not isinstance(data_list, list):
                    raise ValueError("data.json 应该包含消息列表。")
                for item in data_list:
                    self.process_message(item)
                # 处理完毕后清空文件
                with open(self.file_path, 'w', encoding='utf-8') as file:
                    file.write('')
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"读取文件 {self.file_path} 出错: {e}")
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")

    def process_message(self, data):
        try:
            receiver_names = data.get("receiver_name", [])
            content = data.get("message", "")
            group_names = data.get("group_name", [])

            self.send_message(receiver_names, content, group_names)
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")

    def send_message(self, receiver_names, content, group_names=None):
        """
        根据配置的 channel_type 发送消息。
        """
        if self.channel_type not in ["wx", "ntchat"]:
            logger.error(f"不支持的 channel_type: {self.channel_type}")
            return

        try:
            if self.channel_type == "wx":
                self._send_itchat_message(receiver_names, content, group_names)
            elif self.channel_type == "ntchat":
                self._send_ntchat_message(receiver_names, content, group_names)
        except Exception as e:
            logger.error(f"发送消息时出错: {e}")
            raise

    def _detect_media_type(self, content):
        """
        根据内容判断媒体类型。
        """
        if content.startswith(("http://", "https://")):
            lower_content = content.lower()
            if lower_content.endswith((".jpg", ".jpeg", ".png", ".gif", ".img")):
                return "img"
            elif lower_content.endswith((".mp4", ".avi", ".mov", ".pdf")):
                return "video"
            elif lower_content.endswith((".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".txt", ".csv")):
                return "file"
            else:
                logger.warning(f"不支持的文件类型: {content}")
                return "unsupported"
        return "text"

    def _send_itchat_message(self, receiver_names, content, group_names):
        """
        使用 itchat 发送消息。
        """
        try:
            self.channel.get_friends(update=True)
            self.channel.get_chatrooms(update=True)

            media_type = self._detect_media_type(content)
            if media_type == "unsupported":
                return

            if group_names:
                for group_name in group_names:
                    chatrooms = self.channel.search_chatrooms(name=group_name)
                    if not chatrooms:
                        raise ValueError(f"未找到群聊: {group_name}")
                    chatroom = chatrooms[0]

                    if receiver_names:
                        for receiver_name in receiver_names:
                            at_content = ""
                            if receiver_name in ["所有人", "all"]:
                                at_content = "@所有人 "
                            else:
                                member = self._find_itchat_member(chatroom, receiver_name)
                                if member:
                                    at_content = f"@{member.NickName} "
                                else:
                                    raise ValueError(f"在群聊 {group_name} 中未找到成员: {receiver_name}")
                            self.send_msg(media_type, content, chatroom.UserName, at_content)
                            logger.info(f"发送消息到 {group_name} 的 {receiver_name}: {content}")
                    else:
                        self.send_msg(media_type, content, chatroom.UserName)
                        logger.info(f"发送消息到群聊 {group_name}: {content}")

            else:
                if receiver_names:
                    for receiver_name in receiver_names:
                        if receiver_name in ["所有人", "all"]:
                            raise ValueError("无法在个人消息中 @ 所有人。")
                        friends = self.channel.search_friends(remarkName=receiver_name) or self.channel.search_friends(name=receiver_name)
                        if friends:
                            self.send_msg(media_type, content, friends[0].UserName)
                            logger.info(f"发送消息到 {friends[0].NickName}: {content}")
                        else:
                            raise ValueError(f"未找到好友: {receiver_name}")
                else:
                    raise ValueError("接收者列表为空，无法发送个人消息。")
        except Exception as e:
            logger.error(f"发送 itchat 消息时出错: {e}")
            raise

    def _find_itchat_member(self, chatroom, member_name):
        """
        在 itchat 群聊中通过名称查找成员。
        """
        for member in chatroom.MemberList:
            # 一些微信名称是不常见字, 会有特殊符号   需要除去
            if member.NickName.replace("\x7f\x7f", "") == member_name or member.DisplayName.replace("\x7f\x7f", "") == member_name:
                return member
        # 如果在群聊中未找到，尝试在好友列表中查找
        friends = self.channel.search_friends(remarkName=member_name) or self.channel.search_friends(name=member_name)
        return friends[0] if friends else None

    def _send_ntchat_message(self, receiver_names, content, group_names):
        """
        使用 ntchat 发送消息。
        """
        try:
            media_type = self._detect_media_type(content)
            if media_type == "unsupported":
                return

            if group_names:
                for group_name in group_names:
                    chatroom = self._find_ntchat_chatroom(group_name)
                    if not chatroom:
                        logger.warning(f"未找到群聊: {group_name}")
                        continue

                    wxid = chatroom.get("wxid")
                    room_members = self.channel.get_room_members(wxid)

                    if receiver_names:
                        if "所有人" in receiver_names or "all" in receiver_names:
                            at_content = f"@所有人 {content}"
                            self.channel.send_room_at_msg(wxid, at_content, [])
                            logger.info(f"发送消息到 {group_name} 的所有人: {content}")
                        else:
                            user_wxids = [self._find_ntchat_member(wxid, name) for name in receiver_names]
                            user_wxids = [uid for uid in user_wxids if uid]

                            if user_wxids:
                                at_content = f"{' '.join([f'@{name}' for name in receiver_names])} {content}"
                                self.channel.send_room_at_msg(wxid, at_content, user_wxids)
                                logger.info(f"发送消息到 {group_name} 的 {receiver_names}: {content}")
                            else:
                                logger.warning(f"在群聊 {group_name} 中未找到成员: {receiver_names}")
                    else:
                        self.channel.send_text(wxid, content)
                        logger.info(f"发送消息到群聊 {group_name}: {content}")

            else:
                for receiver_name in receiver_names:
                    if receiver_name in ["所有人", "all"]:
                        raise ValueError("无法在个人消息中 @ 所有人。")
                    wxid = self._find_ntchat_friend(receiver_name)
                    if wxid:
                        self._send_ntchat_media_or_text(media_type, content, wxid)
                        logger.info(f"发送消息到 {receiver_name}: {content}")
                    else:
                        logger.warning(f"未找到好友: {receiver_name}")
        except Exception as e:
            logger.error(f"发送 ntchat 消息时出错: {e}")
            raise

    def _find_ntchat_chatroom(self, group_name):
        """
        在 ntchat 中通过名称查找群聊。
        """
        rooms = self.channel.get_rooms()
        for room in rooms:
            if room.get("nickname") == group_name:
                return room
        return None

    def _find_ntchat_member(self, group_wxid, member_name):
        """
        在 ntchat 群聊中通过名称查找成员 wxid。
        """
        room_members = self.channel.get_room_members(group_wxid)
        for member in room_members.get("member_list", []):
            if member.get("nickname") == member_name:
                return member.get("wxid")
        return None

    def _find_ntchat_friend(self, friend_name):
        """
        在 ntchat 中通过名称查找好友的 wxid。
        """
        contacts = self.channel.get_contacts()
        for friend in contacts:
            if friend.get("nickname") == friend_name or friend.get("remark") == friend_name:
                return friend.get("wxid")
        return None

    def _send_ntchat_media_or_text(self, media_type, content, wxid):
        """
        使用 ntchat 根据消息类型发送文本、图片、视频或文件。
        """
        if media_type == "text":
            self.channel.send_text(wxid, content)
        else:
            file_path = self.download_file(content)
            if not file_path:
                raise ValueError(f"无法下载文件: {content}")

            try:
                if media_type == "img":
                    self.channel.send_image(wxid, file_path)
                elif media_type == "video":
                    self.channel.send_video(wxid, file_path)
                elif media_type == "file":
                    self.channel.send_file(wxid, file_path)
                else:
                    logger.error(f"不支持的消息类型: {media_type}")
            finally:
                if os.path.exists(file_path):
                    os.remove(file_path)

    def send_msg(self, msg_type, content, to_user_name, at_content=None):
        """
        使用 itchat 发送消息。
        :param msg_type: 消息类型
        :param content: 消息内容
        :param to_user_name: 接收者的 UserName
        :param at_content: @ 的内容
        """
        try:
            if msg_type == 'text':
                message = f"{at_content}{content}" if at_content else content
                self.channel.send(message, to_user_name)
            elif msg_type in ['img', 'video', 'file']:
                local_file_path = self.download_file(content)
                if not local_file_path:
                    raise ValueError(f"无法下载文件: {content}")

                if at_content:
                    self.channel.send(at_content, to_user_name)

                if msg_type == 'img':
                    self.channel.send_image(local_file_path, to_user_name)
                elif msg_type == 'video':
                    self.channel.send_video(local_file_path, to_user_name)
                elif msg_type == 'file':
                    self.channel.send_file(local_file_path, to_user_name)

                os.remove(local_file_path)
            else:
                raise ValueError(f"不支持的消息类型: {msg_type}")
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            raise

    def download_file(self, url):
        """
        下载文件到本地。
        :param url: 文件的 URL
        """
        try:
            response = requests.get(url, timeout=22)
            response.raise_for_status()
            file_name = os.path.basename(url)
            local_path = os.path.join(os.getcwd(), file_name)
            with open(local_path, 'wb') as file:
                file.write(response.content)
            return local_path
        except Exception as e:
            logger.error(f"从 {url} 下载文件时出错: {e}")
            return None

    def get_help_text(self, **kwargs):
        return (
            "1. Watchdog 文件变化监听插件:\n"
            "   - 监听 data.json 文件变化并发送微信通知。（默认启动）\n"
            "   - 启动监听: $start watchdog\n"
            "   - 停止监听: $stop watchdog\n"
            "   - 查看状态: $check watchdog\n\n"
            "2. 微信命令发送消息:\n"
            "   - $send_msg [微信备注名1, 微信备注名2] 消息内容\n"
            "   - $send_msg [微信备注名1, 微信备注名2] 消息内容 group[群聊1, 群聊2]\n"
            "   - $send_msg [所有人] 消息内容 group[群聊1, 群聊2]"
        )




