"""Đồ thị LangGraph của agent. Đồ thị chỉ quản lý vòng lặp và điều kiện dừng, không chứa các bước mua hàng.

Bản tuần 3, bước đầu: agent ⇄ tools, kết thúc khi agent nộp báo cáo hoặc chạm giới hạn.
`wait_human` và checkpointer được thêm ở bước sau.
"""

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from agent.harness.guard import MAX_STEPS
from agent.harness.tools_node import make_tools_node
from agent.prompts import NUDGE, SYSTEM_PROMPT
from agent.state import AgentState
from agent.tool_specs import tool_specs


def run_config(thread_id: str, max_steps: int = MAX_STEPS) -> dict:
    # Mỗi bước tốn 2 lượt node (agent rồi tools). Mặc định của LangGraph là 25 lượt, không đủ cho 40 bước.
    # Giới hạn bước thật nằm ở lớp bảo vệ; con số này chỉ là chốt chặn cuối.
    return {"recursion_limit": 2 * max_steps + 20, "configurable": {"thread_id": thread_id}}


def route_after_agent(state: AgentState) -> str:
    if state["messages"][-1].tool_calls:
        return "tools"
    # Agent trả lời bằng chữ mà không nộp báo cáo: nhắc một lần, lần sau thì dừng.
    return "nudge" if not state["nudged"] else END


def route_after_tools(state: AgentState) -> str:
    if state["stop_reason"] is not None or state["final_report"] is not None:
        return END
    return "agent"


def build_graph(model, tools: dict[str, BaseTool], checkpointer=None, max_steps: int = MAX_STEPS):
    bound = model.bind_tools(tool_specs(tools))

    async def agent(state: AgentState) -> dict:
        response = await bound.ainvoke([SystemMessage(content=SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response]}

    def nudge(state: AgentState) -> dict:
        return {"messages": [HumanMessage(content=NUDGE)], "nudged": True}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", make_tools_node(tools, max_steps))
    graph.add_node("nudge", nudge)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, ["tools", "nudge", END])
    graph.add_conditional_edges("tools", route_after_tools, ["agent", END])
    graph.add_edge("nudge", "agent")
    return graph.compile(checkpointer=checkpointer)
