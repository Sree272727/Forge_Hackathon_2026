import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function Home() {
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();

  if (isAuthenticated) {
    navigate('/dashboard');
    return null;
  }

  return (
    <div className="min-h-screen bg-[#0B021C] relative overflow-hidden">
      <div className="absolute inset-0 opacity-[0.13]">
        <div
          className="absolute inset-0"
          style={{
            backgroundImage: `
              linear-gradient(to right, #D0D5DD 1px, transparent 1px),
              linear-gradient(to bottom, #D0D5DD 1px, transparent 1px)
            `,
            backgroundSize: '152px 152px',
          }}
        />
      </div>

      <div className="absolute -bottom-[400px] -left-[400px] w-[800px] h-[800px] bg-[#8243EA] opacity-40 rounded-full blur-[300px] pointer-events-none" />
      <div className="absolute bottom-[200px] left-[200px] w-[600px] h-[600px] bg-[#8243EA] opacity-20 rounded-full blur-[300px] pointer-events-none" />

      <div className="absolute top-12 left-[28%] w-2 h-2 bg-gray-400 opacity-40 rounded-full" />
      <div className="absolute top-52 left-[19%] w-2 h-2 bg-gray-400 opacity-40 rounded-full" />
      <div className="absolute top-40 left-[30%] w-2 h-2 bg-gray-400 opacity-40 rounded-full" />
      <div className="absolute top-72 left-[40%] w-2 h-2 bg-gray-400 opacity-40 rounded-full" />
      <div className="absolute top-88 left-[48%] w-2 h-2 bg-gray-400 opacity-40 rounded-full" />
      <div className="absolute top-64 left-[54%] w-2 h-2 bg-gray-400 opacity-40 rounded-full" />

      <div className="relative z-10 min-h-screen flex flex-col">
        <header className="px-8 py-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-[#8243EA] rounded-lg flex items-center justify-center">
              <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
            </div>
            <span className="text-white font-semibold text-xl">ExcelAI</span>
          </div>

          <Link
            to="/login"
            className="px-5 py-2.5 text-sm font-semibold text-gray-200 border border-white/15 rounded-lg hover:bg-white/5 transition-colors"
          >
            Login
          </Link>
        </header>

        <main className="flex-1 px-4 md:px-8 pb-8">
          <div className="max-w-7xl mx-auto">
            <section className="pt-6 md:pt-10">
              <div className="max-w-4xl">
                <p className="inline-flex items-center gap-2 text-xs tracking-widest uppercase text-[#C4B5FD] bg-[#1a1a2e]/60 border border-white/10 rounded-full px-3 py-1 mb-4">
                  AI Analytics for Modern Teams
                </p>
                <h1 className="text-white text-4xl md:text-6xl font-bold mb-6 leading-tight">
                  Your spreadsheet workflow,
                  <br />
                  <span className="text-[#A78BFA]">reimagined with AI copilots.</span>
                </h1>
                <p className="text-[#C4B5FD] text-lg md:text-xl leading-relaxed max-w-3xl">
                  ExcelAI helps operations, finance, and product teams upload workbooks, ask natural-language questions,
                  and get data-backed answers with context from every sheet and table.
                </p>


              </div>

              <div id="features" className="mt-10 grid grid-cols-1 md:grid-cols-3 gap-4">
                <article className="bg-[#1a1a2e]/65 backdrop-blur-sm border border-white/10 rounded-2xl p-5">
                  <div className="w-12 h-12 rounded-xl bg-[#252542] border border-white/10 flex items-center justify-center mb-4">
                    <svg className="w-7 h-7 text-[#A78BFA]" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16V8m5 8V4m5 12v-6M4 20h16" />
                    </svg>
                  </div>
                  <h3 className="text-white font-semibold mb-2">Instant Insight Engine</h3>
                  <p className="text-gray-400 text-sm">Ask questions in plain English and get trend summaries, anomalies, and KPI highlights in seconds.</p>
                </article>

                <article className="bg-[#1a1a2e]/65 backdrop-blur-sm border border-white/10 rounded-2xl p-5">
                  <div className="w-12 h-12 rounded-xl bg-[#252542] border border-white/10 flex items-center justify-center mb-4">
                    <svg className="w-7 h-7 text-[#A78BFA]" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7h16M4 12h16M4 17h10" />
                    </svg>
                  </div>
                  <h3 className="text-white font-semibold mb-2">Sheet-Aware Intelligence</h3>
                  <p className="text-gray-400 text-sm">Automatically reads workbook tabs and metadata so analysis remains structured and traceable.</p>
                </article>

                <article className="bg-[#1a1a2e]/65 backdrop-blur-sm border border-white/10 rounded-2xl p-5">
                  <div className="w-12 h-12 rounded-xl bg-[#252542] border border-white/10 flex items-center justify-center mb-4">
                    <svg className="w-7 h-7 text-[#A78BFA]" viewBox="0 0 24 24" fill="none" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 3v18h18M7 13l3-3 3 2 4-5" />
                    </svg>
                  </div>
                  <h3 className="text-white font-semibold mb-2">Action-Ready Output</h3>
                  <p className="text-gray-400 text-sm">Turn raw worksheets into clear recommendations your team can review and execute quickly.</p>
                </article>
              </div>

              <div className="mt-6 bg-[#1a1a2e]/60 border border-white/10 rounded-2xl p-4 md:p-5">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div className="bg-[#252542]/70 rounded-xl border border-white/10 p-4">
                    <p className="text-xs text-gray-400 mb-3">Upload</p>
                    <svg className="w-full h-24" viewBox="0 0 220 90" fill="none">
                      <rect x="8" y="8" width="204" height="74" rx="10" fill="#1a1a2e" stroke="#ffffff1a" />
                      <rect x="26" y="24" width="70" height="12" rx="6" fill="#6366F1" />
                      <rect x="26" y="44" width="125" height="8" rx="4" fill="#ffffff22" />
                      <rect x="26" y="58" width="100" height="8" rx="4" fill="#ffffff16" />
                      <path d="M178 61V33m0 0-8 8m8-8 8 8" stroke="#A78BFA" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </div>

                  <div className="bg-[#252542]/70 rounded-xl border border-white/10 p-4">
                    <p className="text-xs text-gray-400 mb-3">Analyze</p>
                    <svg className="w-full h-24" viewBox="0 0 220 90" fill="none">
                      <rect x="8" y="8" width="204" height="74" rx="10" fill="#1a1a2e" stroke="#ffffff1a" />
                      <circle cx="55" cy="46" r="20" fill="#8243EA33" stroke="#A78BFA" />
                      <path d="M55 34v12l9 6" stroke="#C4B5FD" strokeWidth="3" strokeLinecap="round" />
                      <rect x="95" y="30" width="95" height="8" rx="4" fill="#ffffff22" />
                      <rect x="95" y="46" width="70" height="8" rx="4" fill="#ffffff16" />
                      <rect x="95" y="62" width="82" height="8" rx="4" fill="#6366F1" />
                    </svg>
                  </div>

                  <div className="bg-[#252542]/70 rounded-xl border border-white/10 p-4">
                    <p className="text-xs text-gray-400 mb-3">Visualize</p>
                    <svg className="w-full h-24" viewBox="0 0 220 90" fill="none">
                      <rect x="8" y="8" width="204" height="74" rx="10" fill="#1a1a2e" stroke="#ffffff1a" />
                      <rect x="30" y="52" width="16" height="20" rx="4" fill="#6366F1" />
                      <rect x="54" y="42" width="16" height="30" rx="4" fill="#7C3AED" />
                      <rect x="78" y="32" width="16" height="40" rx="4" fill="#A78BFA" />
                      <path d="M122 63c13-19 24-27 40-20 10 4 16 2 24-7" stroke="#C4B5FD" strokeWidth="3" strokeLinecap="round" />
                    </svg>
                  </div>
                </div>
              </div>
            </section>
          </div>
        </main>

        <footer className="px-8 py-6 text-center">
          <p className="text-[#A78BFA]/50 text-sm">Powered by AI-driven analytics</p>
        </footer>
      </div>
    </div>
  );
}
