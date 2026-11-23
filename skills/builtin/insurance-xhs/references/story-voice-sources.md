# 故事、人话与开头：来源与取舍

2026-11-17阅读五个GitHub项目后整理。HEAD链接可能变化。以下是阅读摘要与保险场景的独立设计记录，不是原skill的全文、译文或可替代原包的复制件；本包不包含原仓库程序、检测器或付费服务。

## 1. 文子小红书真人化改稿

[SKILL.md](https://github.com/wenziai/wenzi-xiaohongshu-workflow/blob/HEAD/xhs-humanize-compliance/SKILL.md)

学习重点：空泛感、表达效率、材料证据与作者口吻应一起判断，不能只换口语词。保险版把这些落实为：从用户实际材料找具体问题，每段推进理解，并对照标题检查正文是否回答。

未采用：固定诊断数量、强制三点以内、每篇硬加亲历、固定6—8页图文，以及AI直接判“可发”。页数与结构按实际请求；发布仍按机构流程。

## 2. Humanizer-zh

[SKILL.md](https://github.com/op7418/Humanizer-zh/blob/HEAD/SKILL.md)

学习重点：润色不能改变事实的范围、时间、归因和确定程度，不能为了像人而添加经历。保险版据此逐项核对合同期间、限制条件、判断与事实，保留本来清楚的句子。

未采用词语黑名单或“检测通过”目标；连接词、排比、标点根据实际作用判断。该项目当前版本本身也强调这些边界。

## 3. viral-content-rewrite

[SKILL.md](https://github.com/lkttle/viral-content-rewrite/blob/HEAD/SKILL.md) · [叙事策略](https://github.com/lkttle/viral-content-rewrite/blob/HEAD/rewrite-strategies.md)

学习重点：不同材料适合不同组织方式，过程叙事要有进展，零散材料要围绕同一个问题。保险版用“发生的事、实际动作、新信息、当前判断”检查已有材料，不规定必须反转或最终成功。

未采用：标题决定80%打开率、固定3秒标准、强制类比、每章金句、固定升华和互动；不能把这些经验说法当平台规律。医学和保险合同类比还须核对是否造成误解。

## 4. dbs-title-cover-intro

[SKILL.md](https://github.com/dontbesilent2025/dbskill/blob/HEAD/skills/dbs-title-cover-intro/SKILL.md) · [开头与正文衔接](https://github.com/dontbesilent2025/dbskill/blob/HEAD/skills/dbs-title-cover-intro/references/opening-continuity.md)

阅读时关注：标题、封面与口播是不同展示位置；未拍与已拍的改动空间不同。保险版沿用本包既有的“开头推进问题”原则，并独立增加原话与补写分开、无时间码不编切点等素材处理要求。

本项目当前许可限制转载、再分发和打包。仅记录来源和阅读判断，不复制其规则原文、模板、文件或特有工作流进入本包。不移植多平台整套交付、分层选题菜单及作者命名的方法体系。

## 5. mohen 墨痕

[仓库](https://github.com/cxzxx783/mohen) · [写作工具箱](https://github.com/cxzxx783/mohen/blob/HEAD/lib/02-%E5%8E%BBAI%E5%91%B3%E5%86%99%E4%BD%9C.md)

阅读时关注：仅清理套话不足以使文章有作者特点，具体材料比空泛赞美有用。保险版继续从本包已有的真实素材卡提取信息，不复制其工具库、评分或结构。

该项目标注CC BY-NC-SA 4.0及禁止商用，不将其文字、脚本、示例或专有模板复制进学员包。未采用固定第一人称次数、强制情绪和插叙、AI味率计算、emoji密度、平台权重等要求。也不采用把客观功效改成个人体验以绕过约束的做法。

## 统一后的判断顺序

先确保材料和条件真实，再找出本篇的情绪与观点、要回答的问题，按材料决定是否讲故事，然后处理开头、后文进展和语气。用户最新强调情绪和观点，执行细则见[情绪与观点](emotion-and-opinion.md)；剔除机械情绪次数不代表删去作者情绪。没有亲历也能写清楚；有故事也不能取代条款依据。所有来源中的数字化流量说法、自动评分和外部发布动作均不自动继承。

本轮新增的教学示例由本项目重新编写，并显式标注教学属性；不把示意场景变成用户或客户的真实经历。
