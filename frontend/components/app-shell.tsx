"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const navItems = [
  { href: "/", title: "对话工作台", description: "多 Agent 客服对话与诊断" },
  { href: "/knowledge", title: "知识库", description: "检索测试、上传与内容导入" },
  { href: "/monitor", title: "监控中心", description: "Agent、工具与告警状态" },
  { href: "/skills", title: "Skills", description: "规则注入与热加载管理" },
  { href: "/eval", title: "评测实验室", description: "意图识别与端到端评测" }
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-badge">EchoMind v2</span>
          <h1>Enterprise AI Desk</h1>
          <p>给多 Agent 智能客服准备的一块前台控制面板，适合演示、联调和日常运营观察。</p>
        </div>

        <nav className="nav">
          {navItems.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`nav-link${active ? " active" : ""}`}
              >
                <strong>{item.title}</strong>
                <span>{item.description}</span>
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-note">
          推荐先打开“对话工作台”发一条消息，再到“监控中心”看 Agent 统计变化，联调体验会比较完整。
        </div>
      </aside>

      <main className="content">{children}</main>
    </div>
  );
}
