import { Component, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button } from "./ui";

export class PageBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <section role="alert" className="panel" style={{ padding: 24 }}>
        <h2>Не удалось открыть раздел</h2>
        <p className="muted">
          Проверьте соединение и перезагрузите приложение. Вы также можете перейти в другой раздел через меню.
        </p>
        <div className="head-actions">
          <Button onClick={() => window.location.reload()}>Перезагрузить приложение</Button>
          <Link className="button secondary" to="/dashboard">Вернуться к обзору</Link>
        </div>
      </section>
    );
  }
}
