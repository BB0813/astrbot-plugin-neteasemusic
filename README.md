# AstrBot 网易云音乐点歌插件-优化版

[![version](https://img.shields.io/badge/version-2.1.0-blue.svg)](https://github.com/bb0813/astrbot-plugin-neteasemusic)
[![license](https://img.shields.io/github/license/bb0813/astrbot-plugin-neteasemusic.svg)](LICENSE)

这是一款为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 设计的，功能强大且体验豪华的网易云音乐点歌插件。

> **基于原项目二改优化**  
> 原项目：[NachoCrazy/netease-music-astrbot-plugin](https://github.com/NachoCrazy/netease-music-astrbot-plugin)  
> 本项目适配 [Netease-CDN-Bypass](https://github.com/BB0813/Netease-CDN-Bypass) 作为后端 API，彻底解决网易云防盗链问题，播放更稳定。

## ✨ 功能亮点

- **交互式点歌**：通过关键词搜索歌曲，机器人会返回一个列表供您选择。
- **豪华信息卡片**：播放时，会发送包含**歌曲封面**、**详细信息**（歌名、歌手、专辑、时长）的精美图文卡片。
- **语音/文件播放**：
  - 第三方机器人：优先 `Record` 语音
  - QQ 官方机器人：先本地下载音频，再按官方富媒体流程发送 `File` 文件卡片（`file_type=4` → `msg_type=7`）；失败再尝试 `Record`/链接
- **智能音质选择**：支持 lossless（无损）/ exhigh（极高）/ higher（高）/ standard（标准）四种音质。
- **多种触发方式**：支持命令（如 `/点歌`）和自然语言（如 `来一首...`）两种方式点歌。
- **WebUI配置**：可在 AstrBot 的网页后台轻松配置各项参数。
- **CDN 防盗链绕过**：使用 Netease-CDN-Bypass 作为后端，彻底解决网易云 CDN 防盗链问题。

## ⚙️ 安装与配置

### 依赖

本插件依赖外部的 **[Netease-CDN-Bypass](https://github.com/BB0813/Netease-CDN-Bypass)** 服务。请您务必先根据其文档自行部署该服务。

- **API 仓库地址**: [https://github.com/BB0813/Netease-CDN-Bypass](https://github.com/BB0813/Netease-CDN-Bypass)

推荐的部署方式是使用 Docker 或直接运行 Node.js 服务。

### 安装

1. 在 AstrBot 的插件商店中搜索 `netease_music_enhanced` 并安装。
2. 或者，直接将本项目克隆到您的 AstrBot `data/plugins` 目录下。

### 配置

安装并重启 AstrBot 后，在网页后台的 **插件配置** -> **`netease_music_enhanced`** 中进行设置：

1. **API 地址**：填写您部署的 Netease-CDN-Bypass 服务的地址（默认 `http://127.0.0.1:3002`）。
2. **音质**：选择您希望优先播放的音质（lossless 无损 / exhigh 极高 / higher 高 / standard 标准）。
3. **搜索结果数量**：每次搜索返回的歌曲数量。

## 📝 使用方法

- **命令点歌**：
  ```
  /点歌 歌曲名
  ```
  (别名: `/music`, `/听歌`, `/网易云`)

- **自然语言点歌**：
  ```
  来一首 晴天
  播放 稻香
  听听 七里香
  ```

- **选择歌曲**：
  在机器人返回搜索列表后，直接回复您想听的歌曲对应的**数字**即可。

## 💖 致谢

- 感谢 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供了如此强大的机器人框架。
- 感谢原作者 [NachoCrazy](https://github.com/NachoCrazy) 的原项目。
- 感谢 [BB0813](https://github.com/BB0813) 的 [Netease-CDN-Bypass](https://github.com/BB0813/Netease-CDN-Bypass) 项目，让网易云音乐在聊天中播放更稳定。

---
*Based on original work by NachoCrazy · Optimized by BB0813*
