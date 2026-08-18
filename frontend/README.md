# EchoMind Frontend

独立的 Next.js 前端。浏览器默认直接访问当前主机的 `8000` 端口上的 FastAPI 后端。

## 启动

```bash
npm install
npm run dev
```

打开 `http://localhost:3000`。

如果后端地址不是当前主机的 `8000` 端口，可以在构建/启动前设置：

```bash
NEXT_PUBLIC_API_BASE_URL=http://your-host:8000 npm run dev
```
