import logging
from typing import Dict, List, Optional
from hashlib import sha256

from PySide6.QtCore import QObject, Signal

from .utils.model_size_cauculator import ModelCalculator, ModelConfig


class Sakura:
    """Sakura 模型基础信息"""

    repo: str
    filename: str
    sha256: str
    size: float
    minimal_gpu_memory_gib: int  # NOTE(kuriko): zero means no minimum requirement
    recommended_np: Dict[int, int] = {8: 1, 10: 1, 12: 1, 16: 1, 24: 1}
    download_links: Dict[str, str] = {}
    base_model_hf: str  # HuggingFace 模型ID
    bpw: float  # bytes per weight
    config_cache: Optional[Dict] = None  # 模型配置缓存

    def __init__(
        self,
        repo,
        filename,
        sha256,
        size,
        minimal_gpu_memory_gib,
        recommended_np,
        base_model_hf,
        bpw,
        config_cache,
    ):
        self.repo = repo
        self.filename = filename
        self.sha256 = sha256
        self.size = size
        self.minimal_gpu_memory_gib = minimal_gpu_memory_gib
        self.recommended_np = recommended_np
        self.base_model_hf = base_model_hf
        self.bpw = bpw
        self.config_cache = config_cache
        self.download_links = {
            "HFMirror": f"https://hf-mirror.com/SakuraLLM/{repo}/resolve/main/{filename}",
            "HuggingFace": f"https://huggingface.co/SakuraLLM/{repo}/resolve/main/{filename}",
        }

    def to_model_config(self, context: int = 8192) -> ModelConfig:
        """转换为 ModelCalculator 可用的配置"""
        return ModelConfig(
            hf_model=self.base_model_hf,
            context=context,
            batch_size=512,
            bytes_per_weight=self.bpw,
            # 如果有缓存配置，直接传入
            config_cache=self.config_cache,
        )

    def check_sha256(self, file: str) -> bool:
        """验证文件SHA256"""
        sha256_hash = sha256()
        with open(file, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest() == self.sha256


class SakuraCalculator:
    """Sakura 模型资源计算器"""

    def __init__(self, sakura: Sakura):
        self.sakura = sakura

    def calculate_memory_requirements(self, context_length: int) -> Dict[str, float]:
        """计算指定配置下的内存需求"""
        config = self.sakura.to_model_config(context_length)
        calculator = ModelCalculator(config)
        return calculator.calculate_sizes()

    def recommend_config(self, available_memory_gib: float) -> Dict[str, int]:
        """根据可用显存推荐配置"""

        best_config = {"context_length": 1536, "n_parallel": 1}

        # 从16遍历到1，找到最大的n_parallel值
        for np in range(16, 0, -1):
            ctx = 1536 * np  # 确保每个线程至少有1536的上下文长度
            mem_req = self.calculate_memory_requirements(ctx)

            if mem_req["total_size_gib"] <= available_memory_gib:
                best_config["context_length"] = ctx
                best_config["n_parallel"] = np
                logging.info(f"推荐配置: {best_config}")
                break  # 找到合适的配置后退出循环
            else:
                logging.debug(f"配置不满足: context_length={ctx}, n_parallel={np}, total_size_gib={mem_req['total_size_gib']}")

        return best_config


class SakuraList(QObject):
    DOWNLOAD_SRC = [
        "HFMirror",
        "HuggingFace",
    ]

    _list: List[Sakura] = []
    changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)

    def update_sakura_list(self, data_json):
        sakura_list = []
        for obj in data_json["sakura"]:
            sakura = Sakura(
                repo=obj["repo"],
                filename=obj["filename"],
                sha256=obj["sha256"],
                minimal_gpu_memory_gib=obj["minimal_gpu_memory_gib"],
                size=obj["size"],
                recommended_np=obj["recommended_np"],
                base_model_hf=obj["base_model_hf"],
                bpw=obj["bpw"],
                config_cache=obj["config_cache"],
            )
            sakura_list.append(sakura)
        self._list = sakura_list
        self.changed.emit(sakura_list)

    def __getitem__(self, name) -> Sakura:
        for model in self._list:
            if model.filename == name:
                return model
        return None

    def __iter__(self):
        for item in self._list:
            yield item


SAKURA_LIST = SakuraList()
