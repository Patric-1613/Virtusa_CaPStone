import type { CSSProperties } from "react";

const models = [
  { name: "Claude", icon: "✦", colors: ["#7B5CFF", "#B45CFF"] },
  { name: "GPT-4", icon: "◎", colors: ["#00D9E8", "#1473E6"] },
  { name: "Gemini", icon: "✧", colors: ["#5B8CFF", "#B65CFF"] },
  { name: "DeepSeek", icon: "◈", colors: ["#22B7E8", "#4055D8"] },
  { name: "Llama", icon: "∞", colors: ["#5675FF", "#7B5CFF"] },
  { name: "Grok", icon: "𝕏", colors: ["#F58025", "#D84A5C"] },
];

const articles = [
  {
    headline: "Reasoning systems move toward more transparent tool use",
    summary: "New model research focuses on making multi-step tool calls easier to inspect, reproduce and evaluate.",
    source: "Anthropic Research",
    update: true,
  },
  {
    headline: "Open model evaluations put efficiency under the microscope",
    summary: "Fresh benchmarks compare useful task completion against inference cost instead of relying on capability scores alone.",
    source: "Stanford HAI",
    update: false,
  },
  {
    headline: "Scaling studies test whether data quality can beat raw volume",
    summary: "Several labs are examining curated training mixtures as compute budgets and high-quality public data become tighter.",
    source: "arXiv",
    update: false,
  },
];

export default function App() {
  return (
    <main>
      <header className="siteHeader">
        <a className="brand" href="#top" aria-label="AI Daily Digest home">AI Daily Digest</a>
        <span className="today">Friday, 4 September 2026</span>
        <div className="headerActions">
          <label className="srOnly" htmlFor="digest-period">Browse past digests</label>
          <select id="digest-period" defaultValue="today">
            <option value="today">Today</option>
            <option value="yesterday">Yesterday</option>
            <option value="week">This week</option>
          </select>
          <div className="subscribeField">
            <label className="srOnly" htmlFor="email">Email address</label>
            <input id="email" type="email" placeholder="you@example.com" />
            <button type="button">Subscribe</button>
          </div>
        </div>
      </header>

      <div className="pageShell" id="top">
        <section className="hero" aria-labelledby="digest-heading">
          <p className="eyebrow">Illustrative local fixture · no live API</p>
          <h1 id="digest-heading">The signal in AI,<span> without the noise.</span></h1>
          <p className="heroCopy">A concise, source-aware digest of the research, policy and products shaping artificial intelligence today.</p>
          <div className="heroMeta" aria-label="Digest statistics">
            <span>16 stories</span><span>6 model families</span><span>40 claims checked</span>
          </div>
        </section>

        <section className="modelSection" aria-labelledby="models-heading">
          <div className="sectionIntro">
            <div><p className="sectionLabel">Models in today&apos;s edition</p><h2 id="models-heading">Follow the systems making news</h2></div>
            <span className="railHint">Scroll to explore →</span>
          </div>
          <div className="modelRail">
            {models.map((model, index) => {
              const tileStyle = {
                "--model-start": model.colors[0],
                "--model-end": model.colors[1],
                "--float-delay": `${index * -0.45}s`,
              } as CSSProperties;
              return (
                <button className="modelTile" key={model.name} style={tileStyle} type="button" aria-label={`Explore ${model.name} updates`}>
                  <span className="modelIcon" aria-hidden="true">{model.icon}</span><span>{model.name}</span>
                </button>
              );
            })}
          </div>
        </section>

        <div className="contentGrid">
          <section className="feed" aria-labelledby="research-heading">
            <div className="feedHeading"><div><p className="sectionLabel">Today&apos;s digest</p><h2 id="research-heading">Research</h2></div><span>01 / 04</span></div>
            <div className="articleList">
              {articles.map((article) => (
                <article className="articleCard" key={article.headline}>
                  <div>{article.update ? <span className="updatePill">● Tracked update</span> : null}<h3>{article.headline}</h3><p>{article.summary}</p></div>
                  <a href="#source" aria-label={`Read at ${article.source}`}>{article.source} →</a>
                </article>
              ))}
            </div>
          </section>

          <aside className="chatCard" aria-labelledby="chat-heading">
            <span className="statusDot" aria-hidden="true" /><p className="sectionLabel">Digest assistant</p><h2 id="chat-heading">Ask about today&apos;s digest.</h2>
            <p className="chatIntro">Get a quick answer grounded in the stories and sources collected for this edition.</p>
            <div className="quickReplies"><button type="button">What&apos;s new in AI regulation?</button><button type="button">Which breakthroughs matter?</button><button type="button">Recent funding rounds</button></div>
            <div className="sampleReply"><span>AI</span><p>Today&apos;s strongest research theme is verifiable reasoning—labs are prioritising traceability alongside raw performance.</p></div>
            <div className="askField"><input aria-label="Ask a question" placeholder="Ask a question…" /><button type="button" aria-label="Send question">↑</button></div>
          </aside>
        </div>
      </div>

      <footer className="trustStrip"><p><strong>40 claims checked today</strong><span>38 sourced</span><span className="pending">2 pending</span></p><a href="#method">How this works →</a></footer>
    </main>
  );
}
