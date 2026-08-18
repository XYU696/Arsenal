import { ReactNode } from "react";

export function Hero({
  title,
  description,
  meta
}: {
  title: string;
  description: string;
  meta?: ReactNode;
}) {
  return (
    <section className="hero">
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {meta ? <div className="hero-meta">{meta}</div> : null}
    </section>
  );
}

export function Panel({
  title,
  subtitle,
  action,
  children
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="card">
      <div className="card-inner">
        <div className="card-header">
          <div>
            <h3 className="card-title">{title}</h3>
            {subtitle ? <p className="card-subtitle">{subtitle}</p> : null}
          </div>
          {action}
        </div>
        {children}
      </div>
    </section>
  );
}

export function StatusPill({
  tone,
  children
}: {
  tone?: "ok" | "warn" | "error";
  children: ReactNode;
}) {
  return <span className={`status-pill${tone ? ` ${tone}` : ""}`}>{children}</span>;
}
