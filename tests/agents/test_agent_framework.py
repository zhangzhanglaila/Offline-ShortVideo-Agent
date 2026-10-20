"""
Phase 0 Agent框架测试 - 验证基础架构是否正常工作。

测试内容：
1. 数据模型测试 (Message, UserRequest, AgentResult)
2. BaseAgent框架测试
3. MessageBus通信测试
"""

import pytest
import asyncio
from datetime import datetime

from core.models import (
    Message,
    UserRequest,
    AgentResult,
    VideoResult,
    create_task_message,
    create_result_message,
    create_error_message,
)
from core.agents import (
    BaseAgent,
    MessageBus,
    get_message_bus,
    reset_message_bus,
)


# ========== 测试用例1: 消息格式 ==========

class TestMessageFormat:
    """测试消息数据模型。"""

    def test_message_creation(self):
        """测试消息创建。"""
        msg = Message(
            sender="agent_1",
            receiver="agent_2",
            msg_type="task",
            task_type="analyze",
            payload={"data": "test"}
        )

        assert msg.sender == "agent_1"
        assert msg.receiver == "agent_2"
        assert msg.msg_type == "task"
        assert msg.task_type == "analyze"
        assert msg.status == "pending"
        assert msg.msg_id is not None
        assert msg.timestamp is not None
        print(f"✅ 消息创建成功: {msg.msg_id}")

    def test_message_status_methods(self):
        """测试消息状态方法。"""
        msg = Message(
            sender="agent_1",
            receiver="agent_2",
            msg_type="task"
        )

        # 测试标记处理中
        msg.mark_processing()
        assert msg.is_processing()
        assert not msg.is_successful()
        assert not msg.is_failed()

        # 测试标记成功
        msg.mark_success({"result": "data"})
        assert msg.is_successful()
        assert msg.result == {"result": "data"}

        # 测试标记失败
        msg2 = Message(sender="a", receiver="b", msg_type="task")
        msg2.mark_failed("Error occurred")
        assert msg2.is_failed()
        assert msg2.error == "Error occurred"
        print("✅ 消息状态方法正常")

    def test_message_factory_functions(self):
        """测试消息工厂函数。"""
        # 任务消息
        task_msg = create_task_message(
            sender="coordinator",
            receiver="agent_1",
            task_type="analyze",
            payload={"input": "data"}
        )
        assert task_msg.msg_type == "task"
        assert task_msg.task_type == "analyze"

        # 结果消息
        result_msg = create_result_message(
            sender="agent_1",
            receiver="coordinator",
            original_msg_id=task_msg.msg_id,
            result={"output": "processed"}
        )
        assert result_msg.msg_type == "result"
        assert result_msg.status == "success"

        # 错误消息
        error_msg = create_error_message(
            sender="agent_1",
            receiver="coordinator",
            original_msg_id=task_msg.msg_id,
            error="Processing failed"
        )
        assert error_msg.msg_type == "error"
        assert error_msg.status == "failed"

        print("✅ 消息工厂函数正常")

    def test_message_to_dict(self):
        """测试消息序列化。"""
        msg = Message(
            sender="a",
            receiver="b",
            msg_type="task",
            payload={"key": "value"}
        )

        msg_dict = msg.to_dict()
        assert isinstance(msg_dict, dict)
        assert msg_dict["sender"] == "a"
        assert msg_dict["receiver"] == "b"

        json_dict = msg.to_json_compatible()
        assert json_dict["msg_type"] == "task"
        print("✅ 消息序列化正常")


# ========== 测试用例2: 数据模型验证 ==========

class TestUserRequest:
    """测试用户请求模型。"""

    def test_valid_request(self):
        """测试有效请求。"""
        request = UserRequest(
            user_input="讲解Python异步编程",
            category="教育讲解",
            style="minimal",
            duration=30
        )

        assert request.validate()
        print(f"✅ 有效请求通过验证: {request.request_id}")

    def test_invalid_requests(self):
        """测试无效请求。"""
        # 空输入
        req1 = UserRequest(
            user_input="",
            category="教育讲解",
            style="minimal",
            duration=30
        )
        assert not req1.validate()

        # 无效分类
        req2 = UserRequest(
            user_input="test",
            category="invalid",
            style="minimal",
            duration=30
        )
        assert not req2.validate()

        # 无效时长
        req3 = UserRequest(
            user_input="test",
            category="教育讲解",
            style="minimal",
            duration=0
        )
        assert not req3.validate()

        print("✅ 无效请求正确识别")


class TestAgentResult:
    """测试Agent结果模型。"""

    def test_result_creation(self):
        """测试结果创建。"""
        result = AgentResult(
            success=True,
            agent_id="agent_1",
            task_type="analyze",
            data={"output": "data"}
        )

        assert result.is_success
        assert not result.is_failed
        assert result.data == {"output": "data"}
        print("✅ 结果创建成功")

    def test_video_result(self):
        """测试视频结果。"""
        video_result = VideoResult(
            request_id="req_001",
            success=True,
            video_path="/output/video.mp4",
            duration=30,
            quality_score=0.85
        )

        assert video_result.success
        assert video_result.quality_score == 0.85
        summary = video_result.get_summary()
        assert "成功" in summary
        print("✅ 视频结果正常")


# ========== 测试用例3: BaseAgent框架 ==========

class SimpleAgent(BaseAgent):
    """简单的测试Agent实现。"""

    async def execute(self, message: Message) -> Message:
        """实现execute方法。"""
        await self.validate_input(message)
        self.set_status("processing")

        # 模拟处理
        await asyncio.sleep(0.1)

        result_data = {
            "input": message.payload,
            "processed": True
        }

        return self.create_success_message(message, result_data)

    async def handle_error(self, error: Exception, message: Message) -> Message:
        """实现error处理。"""
        self.set_status("error")
        return self.create_error_message(message, str(error))


class TestBaseAgent:
    """测试BaseAgent框架。"""

    def test_agent_creation(self):
        """测试Agent创建。"""
        agent = SimpleAgent(agent_id="test_agent", name="SimpleAgent")

        assert agent.agent_id == "test_agent"
        assert agent.name == "SimpleAgent"
        assert agent.status == "idle"
        print(f"✅ Agent创建成功: {agent}")

    def test_agent_execution(self):
        """测试Agent执行。"""
        async def _test():
            agent = SimpleAgent(agent_id="test_agent", name="SimpleAgent")

            message = Message(
                sender="coordinator",
                receiver="test_agent",
                msg_type="task",
                task_type="analyze",
                payload={"data": "test"}
            )

            result = await agent.execute(message)

            assert result.status == "success"
            assert result.msg_type == "result"
            assert result.result is not None
            print("✅ Agent执行成功")

        asyncio.run(_test())

    def test_agent_error_handling(self):
        """测试Agent错误处理。"""
        async def _test():
            agent = SimpleAgent(agent_id="test_agent", name="SimpleAgent")

            message = Message(
                sender="coordinator",
                receiver="test_agent",
                msg_type="task",
                task_type="analyze"
            )

            error = Exception("Test error")
            error_msg = await agent.handle_error(error, message)

            assert error_msg.status == "failed"
            assert "Test error" in error_msg.error
            print("✅ 错误处理正常")

        asyncio.run(_test())


# ========== 测试用例4: MessageBus通信 ==========

def test_message_bus_send_receive():
    """测试消息总线的发送和接收。"""
    async def _test():
        reset_message_bus()
        bus = get_message_bus()

        # 注册Agent
        await bus.register_agent("agent_1")
        await bus.register_agent("agent_2")

        # 创建消息
        msg = Message(
            sender="agent_1",
            receiver="agent_2",
            msg_type="task",
            payload={"data": "test"}
        )

        # 发送消息
        await bus.send(msg)

        # 接收消息
        received = await bus.receive("agent_2", timeout=1.0)

        assert received is not None
        assert received.sender == "agent_1"
        assert received.payload["data"] == "test"
        print("✅ 消息收发正常")

    asyncio.run(_test())


def test_message_bus_timeout():
    """测试消息总线超时。"""
    async def _test():
        reset_message_bus()
        bus = get_message_bus()

        await bus.register_agent("agent_1")

        # 尝试接收不存在的消息
        received = await bus.receive("agent_1", timeout=0.1)

        assert received is None
        print("✅ 超时处理正常")

    asyncio.run(_test())


def test_message_bus_statistics():
    """测试消息总线统计。"""
    async def _test():
        reset_message_bus()
        bus = get_message_bus()

        await bus.register_agent("agent_1")
        await bus.register_agent("agent_2")

        # 发送几条消息
        for i in range(3):
            msg = Message(
                sender="agent_1",
                receiver="agent_2",
                msg_type="task"
            )
            await bus.send(msg)

        stats = bus.get_stats()

        assert stats["agent_count"] == 2
        assert stats["total_messages"] == 3
        print(f"✅ 消息总线统计: {stats['total_messages']} 条消息")

    asyncio.run(_test())


# ========== 集成测试 ==========

def test_full_workflow():
    """测试完整的Agent工作流。"""
    async def _test():
        reset_message_bus()
        bus = get_message_bus()

        # 创建主Agent和子Agent
        coordinator = HelperAgent(agent_id="coordinator", name="Coordinator")
        worker = HelperAgent(agent_id="worker", name="Worker")

        # 注册到消息总线
        await bus.register_agent("coordinator")
        await bus.register_agent("worker")

        # Coordinator发送任务给Worker
        task_msg = create_task_message(
            sender="coordinator",
            receiver="worker",
            task_type="analyze",
            payload={"input": "test data"}
        )

        await bus.send(task_msg)

        # Worker接收并执行任务
        received_task = await bus.receive("worker", timeout=1.0)
        assert received_task is not None

        result_msg = await worker.execute(received_task)

        # 发送结果回Coordinator
        await bus.send(result_msg)

        # Coordinator接收结果
        received_result = await bus.receive("coordinator", timeout=1.0)
        assert received_result is not None
        assert received_result.status == "success"

        print("✅ 完整工作流测试通过")

    asyncio.run(_test())


class HelperAgent(BaseAgent):
    """协助测试的Agent实现（与TestAgent相同功能）。"""

    async def execute(self, message: Message) -> Message:
        """实现execute方法。"""
        await self.validate_input(message)
        self.set_status("processing")

        # 模拟处理
        await asyncio.sleep(0.1)

        result_data = {
            "input": message.payload,
            "processed": True
        }

        return self.create_success_message(message, result_data)

    async def handle_error(self, error: Exception, message: Message) -> Message:
        """实现error处理。"""
        self.set_status("error")
        return self.create_error_message(message, str(error))


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v", "-s"])
