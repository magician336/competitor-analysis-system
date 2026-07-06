"""
RAG 检索增强链模块
基于 Chroma 向量库 + HuggingFace 本地 Embedding 实现竞品历史数据检索
"""

import logging
from typing import List

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

# 本地 Embedding 模型，首次使用时会自动下载
_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def get_embeddings():
    """获取 Embedding 模型实例"""
    return HuggingFaceEmbeddings(model_name=_EMBEDDING_MODEL)


def build_vector_store(documents: List[str], persist_dir: str = "chroma_db") -> Chroma:
    """
    构建并持久化向量知识库
    :param documents: 原始文本列表
    :param persist_dir: Chroma 持久化目录
    :return: Chroma 向量库实例
    """
    try:
        logger.info(f"正在构建向量库，原始文档数: {len(documents)}")

        # 文本分块
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            separators=["\n\n", "\n", "。", " ", ""],
        )
        chunks = splitter.create_documents(documents)
        logger.info(f"分块后文档数: {len(chunks)}")

        # 构建向量库并持久化
        vector_store = Chroma.from_documents(
            documents=chunks,
            embedding=get_embeddings(),
            persist_directory=persist_dir,
        )
        logger.info(f"向量库构建完成，持久化目录: {persist_dir}")
        return vector_store
    except Exception as e:
        logger.error(f"构建向量库失败: {e}")
        raise


def retrieve_context(query: str, persist_dir: str = "chroma_db", top_k: int = 4) -> List[str]:
    """
    根据查询检索相关上下文
    :param query: 查询文本
    :param persist_dir: Chroma 持久化目录
    :param top_k: 返回最相似的文档数量
    :return: 相关文本片段列表
    """
    try:
        logger.info(f"检索查询: {query}")
        vector_store = Chroma(
            persist_directory=persist_dir,
            embedding_function=get_embeddings(),
        )
        docs = vector_store.similarity_search(query, k=top_k)
        return [doc.page_content for doc in docs]
    except Exception as e:
        logger.error(f"检索失败: {e}")
        raise
