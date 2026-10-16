import { useState } from "react";

import { COMPANY_SHORT, usd } from "./format";
import { TxTable } from "./FlowsTab";
import type { CompanyId, Intercompany } from "./types";
import { CompanyTag, FxNote, Loading, useFin, type TabProps } from "./ui";

export function IntercompanyTab({ scope, start, end, meta, tick }: TabProps) {
  const [local, setLocal] = useState(0);
  const { data, error } = useFin<Intercompany>(`/api/finance/intercompany?company=${scope}&start=${start}&end=${end}`, tick + local);
  if (!data) return <Loading error={error} />;
  const ids = [...meta.companies].sort((a, b) => a.sort - b.sort).map((c) => c.id);
  const cell = (from: CompanyId, to: CompanyId) => data.matrix.find((m) => m.from === from && m.to === to)?.usd ?? 0;
  const unmatched = data.unmatched_outgoing.length + data.unmatched_incoming.length;

  return (
    <>
      <FxNote missing={data.missing_fx} />
      <div className="banner fin-info">
        Şirketler arası hareketler gelir ve gidere, konsolide akışa girmez. Eşleşme açıklama veya karşı tarafta diğer şirketin
        adı geçince otomatik yapılır; Nakit akışı sekmesinden elle de işaretlenebilir.
      </div>

      <div className="grid-2">
        <section className="card">
          <div className="card-head">
            <h2>Gönderen × alan</h2>
            <span className="hint">USD · gönderen tarafın çıkış kayıtlarından</span>
          </div>
          <div className="card-body table-wrap">
            <table className="board fin-table fin-matrix">
              <thead>
                <tr>
                  <th>Gönderen ↓ / Alan →</th>
                  {ids.map((id) => (
                    <th key={id} className="num">
                      <CompanyTag id={id} />
                    </th>
                  ))}
                  <th className="num">Toplam gönderilen</th>
                </tr>
              </thead>
              <tbody>
                {ids.map((from) => (
                  <tr key={from}>
                    <td>
                      <CompanyTag id={from} />
                    </td>
                    {ids.map((to) => (
                      <td key={to} className="num">
                        {from === to ? <span className="muted">·</span> : cell(from, to) ? usd(cell(from, to)) : "—"}
                      </td>
                    ))}
                    <td className="num">
                      <b>{usd(ids.reduce((s, to) => s + (to === from ? 0 : cell(from, to)), 0))}</b>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className="card">
          <div className="card-head">
            <h2>Net pozisyon</h2>
            <span className="hint">çift başına, dönem içi</span>
          </div>
          <div className="card-body">
            {data.pairs.length ? (
              <ul className="fin-pairs">
                {data.pairs.map((p) => {
                  const lender = p.net >= 0 ? p.a : p.b;
                  const borrower = p.net >= 0 ? p.b : p.a;
                  return (
                    <li key={`${p.a}-${p.b}`}>
                      <div>
                        <CompanyTag id={p.a} /> ↔ <CompanyTag id={p.b} />
                      </div>
                      <div className="muted">
                        {COMPANY_SHORT[p.a]} → {COMPANY_SHORT[p.b]} {usd(p.a_to_b)} · {COMPANY_SHORT[p.b]} → {COMPANY_SHORT[p.a]} {usd(p.b_to_a)}
                      </div>
                      <div>
                        {p.net === 0 ? (
                          "Denk"
                        ) : (
                          <>
                            {COMPANY_SHORT[lender]}, {COMPANY_SHORT[borrower]}'ye net <b>{usd(Math.abs(p.net))}</b> aktarmış
                          </>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="empty">Veri yetersiz.</div>
            )}
          </div>
        </section>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Karşılığı bulunamayan transferler</h2>
          <span className="hint">karşı şirkette ±5 gün ve ±%2 içinde eşleşen kayıt yok · {unmatched}</span>
        </div>
        <div className="card-body">
          {unmatched ? (
            <>
              <p className="muted fin-note">Yoldaki para, eksik ekstre veya bağlı olmayan hesap olabilir.</p>
              {data.unmatched_outgoing.length ? (
                <>
                  <h3 className="fin-h3">Gönderilmiş, alanda görünmüyor</h3>
                  <TxTable rows={data.unmatched_outgoing} meta={meta} editable onChanged={() => setLocal((n) => n + 1)} />
                </>
              ) : null}
              {data.unmatched_incoming.length ? (
                <>
                  <h3 className="fin-h3">Alınmış, gönderende görünmüyor</h3>
                  <TxTable rows={data.unmatched_incoming} meta={meta} editable onChanged={() => setLocal((n) => n + 1)} />
                </>
              ) : null}
            </>
          ) : (
            <div className="empty">Tüm transferler eşleşti.</div>
          )}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Şirketler arası hareketler</h2>
          <span className="hint">{data.transactions.length} hareket</span>
        </div>
        <div className="card-body">
          <TxTable rows={data.transactions} meta={meta} editable onChanged={() => setLocal((n) => n + 1)} />
        </div>
      </section>
    </>
  );
}
