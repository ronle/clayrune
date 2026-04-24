// Chat Home: one conversation at the center.

function ChatHomeLayout({ voice, onNew }) {
  const [text, setText] = React.useState('');
  const greet = voice === 'pro' ? 'What do you need?' : 'What would you like to get done?';
  const sub = voice === 'pro' ? 'Describe it and I\'ll route it to the right assistant.' : 'Describe it in plain English — I\'ll figure out who on your team should handle it.';

  return (
    <div className="fc-chat-wrap">
      <div className="fc-chat-hero">
        <h1 className="fc-display">{greet}</h1>
        <p>{sub}</p>
      </div>

      <div className="fc-suggestions">
        {window.FC_SUGGESTIONS.map((s, i) => (
          <div key={i} className="fc-suggestion" onClick={() => setText(s.title)}>
            <div className="fc-sugg-emoji">{s.emoji}</div>
            <div>
              <div className="fc-sugg-t">{s.title}</div>
              <div className="fc-sugg-s">{s.sub}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="fc-chat-list">
        {window.FC_CHAT_SEED.map((m, i) => (
          <div key={i} className={'fc-msg ' + m.from}>
            {m.text}
            {m.workcards && (
              <div className="fc-workcards">
                {m.workcards.map((w, j) => (
                  <div key={j} className="fc-workcard">
                    <window.FcChip status={w.status}>{window.statusLabel(w.status, voice)}</window.FcChip>
                    <span className="fc-workcard-assistant">{w.assistant}</span>
                    <span className="fc-workcard-note">· {w.note}</span>
                  </div>
                ))}
              </div>
            )}
            {m.needsAnswer && (
              <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                <button className="fc-btn primary small">Include it</button>
                <button className="fc-btn ghost small">Skip it</button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="fc-composer">
        <input
          placeholder={voice === 'pro' ? 'Type a request…' : 'Tell me what you\'d like…'}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') setText(''); }}
        />
        <button className="fc-send" title="Send">↑</button>
      </div>
    </div>
  );
}

window.ChatHomeLayout = ChatHomeLayout;
