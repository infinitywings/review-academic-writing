# 参考文献与资源

本仓库的设计依据分三类：核心文献（P1 至 P9）、可选工具（T）和数据资源（R）。列出不等于验证：这些工作提供了检查项和实验设计的灵感，没有一篇直接验证"信息受限的模型读者能预测人类对科学稿件的理解困难"。

## 核心文献

- [P1] Du, J., et al. (2024). LLMs Assist NLP Researchers: Critique Paper (Meta-)Reviewing. *EMNLP 2024*. https://aclanthology.org/2024.emnlp-main.292/ 。借用：每条审稿意见都要能指向证据；人机分歧案例。不能据此声称普遍的写作漏检率。
- [P2] Chang, Y., Lo, K., Goyal, T., Iyyer, M. (2024). BooookScore: A Systematic Exploration of Book-Length Summarization in the Era of LLMs. *ICLR 2024*. https://arxiv.org/abs/2310.00785 。借用：片段、澄清问题、错误类别的输出协议。其评价器可看整份摘要，不验证"只看已读前文"。
- [P3] Lee, Y., Jin, H., Kim, T. S., Zhang, S., Laban, P., Liao, Q. V. (2026). KnowSim: Evaluating Information Calibration in LLM Assistants with User Simulators that Learn. arXiv:2608.17150. https://arxiv.org/abs/2608.17150 。借用：显式知识状态与先决关系。其 27/37、26/35 是条件对比的方向一致率，不是认知模拟准确率。
- [P4] Gu, Y., Tafjord, O., Kim, H., Moore, J., Le Bras, R., Clark, P., Choi, Y. (2026). SimpleToM: Exposing the Gap between Explicit ToM Inference and Implicit ToM Application in LLMs. *ICLR 2026*. https://arxiv.org/abs/2410.13648 。借用：识别知识边界与在推理中应用边界要分开验收。针对性提示能大幅改善该基准，所以全文基线必须同样强。
- [P5] Škrjanec, I., Demberg, V. (2026). Language Models That Match Reader Experience Are Better Predictors of Reading Times. *Journal of Memory and Language*, 146, 104677. https://doi.org/10.1016/j.jml.2025.104677 。借用：读者经验必须被操作化，真人阅读数据是外部标准。
- [P6] Liu, N. F., et al. (2024). Lost in the Middle: How Language Models Use Long Contexts. *TACL*, 12, 157–173. https://aclanthology.org/2024.tacl-1.9/ 。借用：信息"可见"不等于"被可靠使用"。不支持任何固定窗口等同于人类工作记忆。
- [P7] Kim, Z. M., et al. (2026). Align to Structure: Aligning Large Language Models with Structural Information. *AAAI 2026*. https://arxiv.org/abs/2504.03622 。借用：篇章结构可以作为独立分析对象。它是结构奖励驱动的强化学习，不是显式规划流程。
- [P8] Wan, K., et al. (2025). A Cognitive Writing Perspective for Constrained Long-Form Text Generation. *Findings of ACL 2025*. https://aclanthology.org/2025.findings-acl.511/ 。借用：规划、生成、验收分离。不保证科学真实性或首次阅读理解。
- [P9] Clark, T. H., Martin, M. B., Mahowald, K. (2026). Why is AI-generated language so hard to understand? arXiv:2610.29162. https://arxiv.org/abs/2610.29162 。借用：与人类加工困难相关的八个可计量特征（宾语关系从句、名词转动词、无生命主语、依存长度、名词堆叠、未见词对、n-gram 困惑度）在当前模型输出里过度出现，可作为候选提名启发式，也提醒审稿人自己的替换句要受同样的检查。预印本，未经同行评审。

## 可选工具

- [T1] MinerU（opendatalab）。https://github.com/opendatalab/MinerU 。本项目实测选用的 PDF 到 Markdown 抽取器（本地 standard 层级，公式 LaTeX、下标、表格保持最好）。许可为 Apache 2.0 加附加条件，使用前核对。
- [T2] Docling（IBM）。https://github.com/docling-project/docling 。MIT，备选抽取器；实测行内下标会压平。
- [T3] Marker（datalab-to）。https://github.com/datalab-to/marker 。代码 Apache 2.0，模型权重为修改版 OpenRAIL-M，商用有门槛。
- [T4] olmOCR（AI2）。https://github.com/allenai/olmocr 。Apache 2.0，需 NVIDIA 显存。
- [T5] TAACO。https://github.com/LCR-ADS-Lab/TAACO 。衔接指标，只用于提名段落。
- [T6] Comprehensive Coherence Calculator。https://github.com/WeizheXu/coherencepip 。语义相关度，只用于提名段落。

## 数据与代码资源

- [R1] ReviewCritique 数据：https://github.com/jiangshdd/ReviewCritique （带 AI 训练限制的许可）。
- [R2] BooookScore 代码与标注：https://github.com/lilakk/BooookScore （MIT；完整书籍另申请）。
- [R3] KnowSim 项目页：https://yoonjoolee.com/knowsim/ ；实现：https://github.com/yjo2lee/knowsim （MIT）；配置溯源：https://github.com/yjo2lee/knowsim/blob/main/PROVENANCE.md ；真人数据 KnowChat：https://huggingface.co/datasets/yjlee36/knowchat-multi-turn-dialogues （CC BY-NC 4.0）。
- [R4] SimpleToM 代码：https://github.com/yulinggu-cs/SimpleToM （Apache 2.0）；数据：https://huggingface.co/datasets/allenai/SimpleToM （ODC-BY）。
- [R5] PoTeC 眼动语料：https://github.com/DiLi-Lab/PoTeC ；原始语料论文 Jakobi 等 (2025) https://doi.org/10.3758/s13428-024-02536-8 。
- [R6] Lost in the Middle 数据与脚本：https://github.com/nelson-liu/lost-in-the-middle （MIT）。
- [R7] StructAlign 仓库：https://github.com/minnesotanlp/StructAlign （核查时为占位仓库）；提示集：https://huggingface.co/datasets/zaemyung/writing_prompts_collection 。
- [R8] CogWriter 实现：https://github.com/KaiyangWan/CogWriter （MIT）；基准 LongGenBench：https://github.com/mozhu621/LongGenBench 。
- [R9] 教学范文（开发用，模型已见过，不能作留出材料）：Gopen & Swan (1990) The Science of Scientific Writing, *American Scientist* 78(6)；Northwestern NuWrite 实验报告范文。

各条目的许可以其仓库或数据卡为准；本文件只记录核查时看到的标注。
