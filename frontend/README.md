# 学道 · 课程任务工作台

基于 React、Vite 与 Tailwind CSS 的统一前端，整合学习通签到、学习通泛雅和智慧树课程任务。

## 技术栈

- React 18.2
- Vite 5.0
- TailwindCSS 3.3
- React Router 6.20
- Lucide React (图标)

## 项目结构

```
frontend/
├── src/
│   ├── pages/           # 页面组件
│   │   ├── Login.jsx           # 登录页
│   │   ├── Register.jsx        # 注册页
│   │   ├── Dashboard.jsx       # 用户仪表盘
│   │   ├── ChaoxingSignin.jsx  # 超星签到
│   │   ├── ChaoxingFanya.jsx   # 超星刷课
│   │   └── Zhihuishu.jsx       # 智慧树刷课
│   ├── components/      # 共享组件
│   ├── utils/           # 工具函数
│   │   ├── api.js              # API 请求封装
│   │   └── auth.js             # JWT 认证工具
│   ├── App.jsx          # 路由配置
│   ├── main.jsx         # 入口文件
│   └── index.css        # 全局样式
├── package.json
├── vite.config.js
├── tailwind.config.js
└── postcss.config.js
```

## 功能特性

- Server profile 使用 JWT 认证，令牌只存储在当前标签页的 `sessionStorage`
- Local profile（Tauri）无需学道账号即可直接进入工作台
- 统一导航、任务状态、Toast 与 light/dark/system 主题
- 响应式布局与键盘可访问的服务切换、页签控制
- 三项服务按路由懒加载，地图和二维码依赖保持独立 chunk

## 路由结构

- `/login` - 登录页
- `/register` - 注册页
- `/dashboard` - 今日课程任务工作台
- `/chaoxing-signin` - 学习通签到
- `/chaoxing-fanya` - 学习通泛雅课程任务
- `/zhihuishu-panel` - 智慧树课程任务

## 开发运行

```bash
npm install
npm run dev
```

访问 http://localhost:3000

## 构建部署

```bash
npm run build
npm run preview
```

## API 集成

前端通过 `/api` 代理访问后端 API（默认 localhost:8000）：

- `POST /api/v1/auth/login` - 登录
- `POST /api/v1/auth/register` - 注册

## 设计系统

- 校园青：`#167C80`（主操作与进行中）
- 墨蓝：`#172033`（正文与夜间基底）
- 荧光橙：`#FF7A45`（下一步与注意事项）
- 讲义纸：`#F4F6F1`（日间背景）
- 中文字体：`PingFang SC` / `Microsoft YaHei` / system-ui
- 视觉主题：课程表 × 夜间自习台；课表网格和今日任务轨道用于表达真实流程状态

## 可访问性

- 文字对比度 >= 4.5:1
- 点击区域 >= 44x44px
- 可点击元素带 cursor-pointer
- 过渡动画 200ms
- 支持键盘导航
