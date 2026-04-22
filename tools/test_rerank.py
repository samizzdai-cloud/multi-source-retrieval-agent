"""Rerank 工具测试脚本

测试场景：
  1. QA 模式：问答检索
  2. 语义相似度模式：FAQ 匹配
  3. RAG 场景：chunk 重排序
  4. 边界情况：空文档、超长文档等
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=True)
from rerank import (
    rerank_documents,
    rerank_for_rag,
    rerank_for_faq,
    INSTRUCT_TEMPLATES,
)


def test_qa_mode():
    """测试 QA 模式：找答案"""
    print("\n" + "="*60)
    print("测试 1: QA 模式 - 问答检索")
    print("="*60)

    query = "如何预防感冒？"
    documents = [
        "勤洗手是预防感冒的有效方法",
        "感冒是一种常见的呼吸道疾病",
        "多喝水、充足睡眠可以增强免疫力",
        "量子计算是计算科学的前沿领域",
        "预防感冒应该注意保暖和锻炼身体",
    ]

    result = rerank_documents(
        query=query,
        documents=documents,
        top_n=3,
        instruct_template="qa",
        return_documents=True,
    )

    print(f"Query: {query}")
    print(f"Status: {result['status']}")
    if result['status'] == 'success':
        print(f"返回 {len(result['results'])} 个结果:")
        for i, item in enumerate(result['results'], 1):
            print(f"\n  {i}. [Score: {item['relevance_score']:.4f}] "
                  f"Index: {item['index']}")
            if 'document' in item:
                print(f"     Text: {item['document'].get('text', '')}")
        print(f"\nUsage: {result['usage']}")
        print(f"Instruct: {result['_debug']['instruct']}")
    else:
        print(f"Error: {result.get('message')}")


def test_semantic_mode():
    """测试语义相似度模式：FAQ 匹配"""
    print("\n" + "="*60)
    print("测试 2: 语义相似度模式 - FAQ 匹配")
    print("="*60)

    user_question = "如何修改密码？"
    faq_questions = [
        "忘记密码怎么办？",
        "如何注册新账号？",
        "怎样更改登录密码？",
        "如何删除账户？",
        "密码重置流程是什么？",
    ]

    result = rerank_documents(
        query=user_question,
        documents=faq_questions,
        top_n=3,
        instruct_template="semantic",
        return_documents=True,
    )

    print(f"User Question: {user_question}")
    print(f"Status: {result['status']}")
    if result['status'] == 'success':
        print(f"最相似的 {len(result['results'])} 个 FAQ:")
        for i, item in enumerate(result['results'], 1):
            print(f"\n  {i}. [Score: {item['relevance_score']:.4f}]")
            if 'document' in item:
                print(f"     FAQ: {item['document'].get('text', '')}")
    else:
        print(f"Error: {result.get('message')}")


def test_rag_scenario():
    """测试 RAG 场景：chunk 重排序"""
    print("\n" + "="*60)
    print("测试 3: RAG 场景 - Chunk 重排序")
    print("="*60)

    query = "Python 异步编程的优势"
    chunks = [
        {"text": "asyncio 是 Python 的异步编程库，支持协程", "source": "doc1"},
        {"text": "requests 是一个同步的 HTTP 客户端库", "source": "doc2"},
        {"text": "异步编程可以提高 I/O 密集型任务的性能", "source": "doc3"},
        {"text": "Django 是一个 Web 框架", "source": "doc4"},
        {"text": "async/await 语法让异步代码更易读", "source": "doc5"},
    ]

    reranked = rerank_for_rag(
        query=query,
        chunks=chunks,
        top_n=3,
        score_threshold=0.2,
    )

    print(f"Query: {query}")
    print(f"原始 chunks: {len(chunks)} 个")
    print(f"重排序后: {len(reranked)} 个")
    for i, chunk in enumerate(reranked, 1):
        print(f"\n  {i}. [Rerank Score: {chunk['rerank_score']:.4f}]")
        print(f"     Text: {chunk['text']}")
        print(f"     Source: {chunk['source']}")


def test_faq_helper():
    """测试 FAQ 便捷函数"""
    print("\n" + "="*60)
    print("测试 4: FAQ 便捷函数")
    print("="*60)

    user_question = "账号被锁定了怎么办？"
    faq_questions = [
        "如何解锁被冻结的账户？",
        "忘记密码怎么办？",
        "账号安全设置在哪里？",
        "如何注销账号？",
        "账户被封禁如何申诉？",
    ]

    matches = rerank_for_faq(
        user_question=user_question,
        faq_questions=faq_questions,
        top_n=2,
    )

    print(f"User Question: {user_question}")
    print(f"匹配到 {len(matches)} 个相似问题:")
    for i, match in enumerate(matches, 1):
        print(f"\n  {i}. [Score: {match['score']:.4f}]")
        print(f"     FAQ: {match['question']}")
        print(f"     Index: {match['index']}")


def test_custom_instruct():
    """测试自定义 instruct"""
    print("\n" + "="*60)
    print("测试 5: 自定义 Instruct - 代码检索")
    print("="*60)

    query = "如何读取文件内容"
    documents = [
        "with open('file.txt', 'r') as f: content = f.read()",
        "Python 文件操作使用 open() 函数",
        "import os; os.path.exists('file.txt')",
        "文件读取需要注意编码问题",
    ]

    result = rerank_documents(
        query=query,
        documents=documents,
        top_n=2,
        instruct_template="code",
        return_documents=True,
    )

    print(f"Query: {query}")
    print(f"Instruct: {INSTRUCT_TEMPLATES['code']}")
    if result['status'] == 'success':
        print(f"\n最相关的 {len(result['results'])} 个代码片段:")
        for i, item in enumerate(result['results'], 1):
            print(f"\n  {i}. [Score: {item['relevance_score']:.4f}]")
            if 'document' in item:
                print(f"     Code: {item['document'].get('text', '')}")


def test_edge_cases():
    """测试边界情况"""
    print("\n" + "="*60)
    print("测试 6: 边界情况")
    print("="*60)

    # 空文档列表
    print("\n6.1 空文档列表:")
    result = rerank_documents(query="test", documents=[])
    print(f"  Status: {result['status']}, Message: {result.get('message')}")

    # 单个文档
    print("\n6.2 单个文档:")
    result = rerank_documents(
        query="test",
        documents=["单个文档"],
        return_documents=True
    )
    if result['status'] == 'success':
        print(f"  返回 {len(result['results'])} 个结果")
        print(f"  Score: {result['results'][0]['relevance_score']:.4f}")

    # 结构化文档输入
    print("\n6.3 结构化文档输入:")
    result = rerank_documents(
        query="Python",
        documents=[
            {"text": "Python 是一门编程语言", "id": "doc1"},
            {"content": "Java 是另一门语言", "id": "doc2"},  # 使用 content 字段
        ],
        top_n=1,
        return_documents=True
    )
    if result['status'] == 'success':
        print(f"  返回 {len(result['results'])} 个结果")
        print(f"  Top 1: {result['results'][0]['document']}")


def main():
    """运行所有测试"""
    print("\n" + "="*60)
    print("Qwen3-Rerank 工具测试")
    print("="*60)

    # 检查 API Key
    if not os.getenv("DASHSCOPE_API_KEY"):
        print("\n⚠️  警告: 未配置 DASHSCOPE_API_KEY 环境变量")
        print("请先设置: export DASHSCOPE_API_KEY='your-api-key'")
        return

    try:
        test_qa_mode()
        test_semantic_mode()
        test_rag_scenario()
        test_faq_helper()
        test_custom_instruct()
        test_edge_cases()

        print("\n" + "="*60)
        print("✅ 所有测试完成")
        print("="*60)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
