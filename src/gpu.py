import os
import platform
import math
import logging
import subprocess
from typing import List, Dict

from .sakura import SAKURA_LIST
from .utils import BytesToGiB
from .utils.gpu import GPUAbility, GPUType, GPUInfo
from .utils.gpu.nvidia import get_nvidia_gpus
from .sakura import SakuraCalculator


class GPUDisplayHelper:
    """GPU显示名称处理助手类"""
    
    @staticmethod
    def create_display_name(gpu_info: GPUInfo, index: int) -> str:
        """创建GPU的显示名称"""
        return f"{gpu_info.name} (GPU {index})" if gpu_info.pci_bus_id else gpu_info.name
    
    @staticmethod
    def parse_display_name(display_name: str) -> tuple[str, int|None]:
        """解析GPU显示名称，返回(gpu_name, index)"""
        if " (GPU " in display_name and ")" in display_name:
            name_part, index_part = display_name.split(" (GPU ")
            try:
                index = int(index_part.rstrip(")"))
                return name_part, index
            except ValueError:
                return display_name, None
        return display_name, None
    
    @staticmethod
    def find_gpu_key(display_name: str, gpu_info_map: Dict[str, GPUInfo]) -> str|None:
        """根据显示名称找到对应的GPU key"""
        # 如果显示名称本身就是key
        if display_name in gpu_info_map:
            return display_name
            
        # 尝试解析显示名称
        gpu_name, gpu_index = GPUDisplayHelper.parse_display_name(display_name)
        
        # 在gpu_info_map中查找匹配的GPU
        matching_keys = []
        for key, info in gpu_info_map.items():
            if info.name == gpu_name:
                matching_keys.append(key)
        
        # 如果找到多个匹配的GPU，使用索引选择
        if matching_keys:
            if gpu_index is not None and gpu_index < len(matching_keys):
                return matching_keys[gpu_index]
            # 如果没有索引或索引无效，返回第一个匹配的
            return matching_keys[0]
        
        return None
    
    @staticmethod
    def match_gpu_name(display_name: str, target_name: str) -> bool:
        """检查目标名称是否匹配显示名称"""
        gpu_name, _ = GPUDisplayHelper.parse_display_name(display_name)
        return target_name in gpu_name

class GPUManager:
    def __init__(self):
        self.gpu_info_map: Dict[str, GPUInfo] = {}
        self.nvidia_gpus = []
        self.amd_gpus = []
        self.intel_gpus = []
        self.detect_gpus()

    @staticmethod
    def get_gpu_index_from_pci(pci_bus_id: str) -> int:
        """从 PCI 总线 ID 中提取设备号作为 GPU 索引"""
        try:
            # PCI 总线 ID 格式为 "00000000:01:00.0"，我们提取设备号（01）作为索引
            if pci_bus_id and ":" in pci_bus_id:
                device_id = pci_bus_id.split(":")[1]
                return int(device_id, 16)  # 将十六进制设备号转换为十进制
        except (IndexError, ValueError) as e:
            logging.warning(f"无法从PCI总线ID提取设备号: {pci_bus_id}, 错误: {e}")
        return 0

    def __add_gpu_to_list(self, gpu_info: GPUInfo):
        if gpu_info.gpu_type == GPUType.NVIDIA:
            # 直接从 PCI 总线 ID 获取 GPU 索引
            gpu_index = self.get_gpu_index_from_pci(gpu_info.pci_bus_id) if gpu_info.pci_bus_id else len(self.nvidia_gpus)
            display_name = GPUDisplayHelper.create_display_name(gpu_info, gpu_index)
            self.nvidia_gpus.append(display_name)
        elif gpu_info.gpu_type == GPUType.AMD:
            self.amd_gpus.append(gpu_info.name)
        elif gpu_info.gpu_type == GPUType.INTEL:
            self.intel_gpus.append(gpu_info.name)

    def __universal_detect_nvidia_gpu(self):
        ''' Detect NVIDIA GPUs using nvidia-smi '''
        self.nvidia_gpus = []
        nvidia_gpu_info = get_nvidia_gpus()
        
        # 处理每个 GPU
        for gpu_info in nvidia_gpu_info:
            # 使用 pci_bus_id 作为唯一标识符
            gpu_key = gpu_info.pci_bus_id if gpu_info.pci_bus_id else gpu_info.name
            # 直接从 PCI 总线 ID 获取 GPU 索引
            gpu_index = self.get_gpu_index_from_pci(gpu_info.pci_bus_id) if gpu_info.pci_bus_id else len(self.nvidia_gpus)
            display_name = GPUDisplayHelper.create_display_name(gpu_info, gpu_index)
            self.nvidia_gpus.append(display_name)
            
            if gpu_key in self.gpu_info_map:
                self.gpu_info_map[gpu_key].merge_from(gpu_info)
                logging.info(f"更新 GPU 信息: {self.gpu_info_map[gpu_key]}")
            else:
                logging.info(f"检测到新的NVIDIA GPU: {gpu_info}")
                self.gpu_info_map[gpu_key] = gpu_info

    def detect_gpus(self):
        ''' platform-specific method to detect GPUs  '''
        if platform.system() == "Windows":
            self.detect_gpus_windows()
        elif platform.system() == "Linux":
            self.detect_gpus_linux()
        else:
            logging.warning("Disable GPU detection on non-windows platform")

    def detect_gpus_linux(self):
        self.__universal_detect_nvidia_gpu()
        return

    def detect_gpus_windows(self):
        # Non stable gpu detection
        try:
            # Detect gpu properties
            from .utils import windows
            adapter_values = windows.get_gpu_mem_info()

            for adapter in adapter_values:
                name = adapter.AdapterString
                gpu_type = self.get_gpu_type(name)

                dedicated_gpu_memory = adapter.MemorySize
                # 使用 pci_bus_id 作为唯一标识符
                gpu_key = adapter.pci_bus_id if hasattr(adapter, 'pci_bus_id') else name
                if gpu_key not in self.gpu_info_map:
                    gpu_info = GPUInfo(
                        index=None,
                        name=name,
                        gpu_type=gpu_type,
                        dedicated_gpu_memory=dedicated_gpu_memory,
                        pci_bus_id=adapter.pci_bus_id if hasattr(adapter, 'pci_bus_id') else None
                    )
                    logging.info(f"检测到 GPU: {gpu_info}")
                    self.__add_gpu_to_list(gpu_info)
                    self.gpu_info_map[gpu_key] = gpu_info
                else:
                    logging.warning(f"重复的 GPU: {gpu_key}, 已存在，忽略")

        except Exception as e:
            logging.warning(f"detect_gpus_properties() 出错: {str(e)}")

        # 检测NVIDIA GPU
        self.__universal_detect_nvidia_gpu()

        # 检测AMD GPU
        try:
            import wmi

            c = wmi.WMI()
            amd_gpus_temp = []
            for gpu in c.Win32_VideoController():
                if "AMD" in gpu.Name or "ATI" in gpu.Name:
                    amd_gpus_temp.append(gpu.Name)
            logging.info(f"检测到AMD GPU(正向列表): {amd_gpus_temp}")
            # 反向添加AMD GPU
            self.amd_gpus = list(reversed(amd_gpus_temp))
            logging.info(f"检测到AMD GPU(反向列表): {self.amd_gpus}")
        except Exception as e:
            logging.error(f"检测AMD GPU时出错: {str(e)}")

    def get_gpu_type(self, gpu_name):
        if "NVIDIA" in gpu_name.upper():
            return GPUType.NVIDIA
        elif "AMD" in gpu_name.upper() or "ATI" in gpu_name.upper():
            return GPUType.AMD
        # TODO(kuriko): add intel gpu support in future
        else:
            return GPUType.UNKNOWN

    def check_gpu_ability(self, gpu_display_name: str, model_name: str, context_length: int = None, n_parallel: int = None) -> GPUAbility:
        # 从显示名称中找到对应的GPU key
        gpu_key = GPUDisplayHelper.find_gpu_key(gpu_display_name, self.gpu_info_map)
        if not gpu_key or gpu_key not in self.gpu_info_map:
            logging.error(f"未找到GPU: {gpu_display_name}, 当前已知GPU: {list(self.gpu_info_map.keys())}")
            return GPUAbility(is_capable=False, reason=f"未找到显卡对应的参数信息")

        gpu_info = self.gpu_info_map[gpu_key]
        if gpu_info.gpu_type not in [GPUType.NVIDIA, GPUType.AMD]:
            return GPUAbility(
                is_capable=False, reason=f"目前只支持 NVIDIA 和 AMD 的显卡"
            )

        if gpu_info.avail_dedicated_gpu_memory is not None:
            ability = self._check_dynamic_memory(gpu_info, model_name, context_length, n_parallel)
        else:
            ability = self._check_static_memory(gpu_info, model_name)

        gpu_info.ability = ability
        return ability

    def _check_dynamic_memory(self, gpu_info: GPUInfo, model_name: str, context_length: int = None, n_parallel: int = None) -> GPUAbility:
        """检查动态可用显存"""
        gpu_mem = gpu_info.avail_dedicated_gpu_memory
        gpu_mem_gib = BytesToGiB(gpu_mem)
        total_mem_gib = BytesToGiB(gpu_info.dedicated_gpu_memory)
        
        model = SAKURA_LIST[model_name]
        if not model:
            return GPUAbility(is_capable=True, reason="")
            
        try:
            calculator = SakuraCalculator(model)
            if context_length is None or n_parallel is None:
                # 如果没有提供参数，使用推荐配置
                config = calculator.recommend_config(gpu_mem_gib)
            else:
                config = {
                    "context_length": context_length,
                    "n_parallel": n_parallel
                }
                
            # 计算实际显存使用
            memory_usage = calculator.calculate_memory_requirements(
                config["context_length"]
            )
            
            if gpu_mem_gib < memory_usage['total_size_gib']:
                return GPUAbility(
                    is_capable=False,
                    reason=f"显卡 {gpu_info.name} 的显存不足\n"
                    f"预计需要 {memory_usage['total_size_gib']:.2f} GiB 显存\n"
                    f"当前系统只有 {gpu_mem_gib:.2f} GiB 剩余显存\n"
                    f"总显存: {total_mem_gib:.2f} GiB"
                )
        except Exception as e:
            logging.warning(f"显存需求计算失败: {e}")
            # 如果计算失败，回退到基本显存检查
            if (gpu_mem_req_gib := model.minimal_gpu_memory_gib) != 0 \
            and gpu_mem_gib < gpu_mem_req_gib:
                return GPUAbility(
                    is_capable=False,
                    reason=f"显卡 {gpu_info.name} 的显存不足\n"
                    f"至少需要 {gpu_mem_req_gib:.2f} GiB 显存\n"
                    f"当前系统只有 {gpu_mem_gib:.2f} GiB 剩余显存"
                )
        
        return GPUAbility(is_capable=True, reason="")

    def _check_static_memory(self, gpu_info: GPUInfo, model_name: str) -> GPUAbility:
        """检查静态总显存"""
        gpu_mem = gpu_info.dedicated_gpu_memory
        gpu_mem_gib = math.ceil(BytesToGiB(gpu_mem)) \
            if gpu_mem > (2**30) else BytesToGiB(gpu_mem)
        
        model = SAKURA_LIST[model_name]
        if (
            model
            and (gpu_mem_req_gib := model.minimal_gpu_memory_gib) != 0
            and gpu_mem_gib < gpu_mem_req_gib
        ):
            return GPUAbility(
                is_capable=False,
                reason=f"显卡 {gpu_info.name} 的显存不足\n"
                f"至少需要 {gpu_mem_req_gib:.2f} GiB 显存\n"
                f"当前显卡总显存为 {gpu_mem_gib:.2f} GiB"
            )
        
        return GPUAbility(is_capable=True, reason="")

    def set_gpu_env(self, env, selected_gpu_display_name, selected_index):
        # 从显示名称中找到对应的GPU key
        gpu_key = GPUDisplayHelper.find_gpu_key(selected_gpu_display_name, self.gpu_info_map)
        if not gpu_key:
            logging.warning(f"未找到GPU: {selected_gpu_display_name}")
            return env
            
        gpu_info = self.gpu_info_map[gpu_key]
        if gpu_info.gpu_type == GPUType.NVIDIA:
            env["CUDA_VISIBLE_DEVICES"] = str(selected_index)
            logging.info(f"设置 CUDA_VISIBLE_DEVICES = {env['CUDA_VISIBLE_DEVICES']}")
        elif gpu_info.gpu_type == GPUType.AMD:
            env["HIP_VISIBLE_DEVICES"] = str((selected_index) - len(self.nvidia_gpus))
            logging.info(f"设置 HIP_VISIBLE_DEVICES = {env['HIP_VISIBLE_DEVICES']}")
        else:
            logging.warning(f"未知的GPU类型: {selected_gpu_display_name}")
        return env
