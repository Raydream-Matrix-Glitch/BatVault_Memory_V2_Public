import React, { useMemo, useState } from "react";
import Card from "./ui/Card";
import Input from "./ui/Input";
import { useSchema } from "../../hooks/useSchema";

/**
 * SchemaExplorer displays the Memory API's field catalog and relations graph in a
 * searchable format. Fields are grouped by semantic name with their
 * synonyms, and relations are presented as a simple table. Cyberpunk
 * styling is applied via existing primitives.
 */
export default function SchemaExplorer() {
  const { fields, relations, loading, error } = useSchema();
  const [search, setSearch] = useState("");

  // Build a filtered list of field groups based on search text
  const filteredFields = useMemo(() => {
    if (!fields) return {};
    const term = search.trim().toLowerCase();
    if (!term) return fields;
    const result: Record<string, string[]> = {};
    Object.entries(fields).forEach(([semantic, synonyms]) => {
      // Match against semantic name or any synonym
      if (
        semantic.toLowerCase().includes(term) ||
        synonyms.some((s) => s.toLowerCase().includes(term))
      ) {
        result[semantic] = synonyms;
      }
    });
    return result;
  }, [fields, search]);

  return (
    <Card className="mt-6">
      <h2 className="text-lg font-semibold text-vaultred mb-2">
        Schema Explorer
      </h2>
      {loading && <p className="text-copy text-sm">Loading schema…</p>}
      {error && (
        <p className="text-red-400 text-sm">
          Error loading schema: {String(error)}
        </p>
      )}
      {fields && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-copy mb-1">
              Search fields
            </label>
            <Input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Type to filter by semantic name or synonym…"
            />
          </div>
          <div className="max-h-48 overflow-y-auto pr-2 border border-gray-700 rounded-md p-2">
            {Object.keys(filteredFields).length > 0 ? (
              Object.entries(filteredFields).map(([semantic, synonyms]) => (
                <div key={semantic} className="mb-2">
                  <div className="text-sm font-semibold text-neonCyan mb-1">
                    {semantic}
                  </div>
                    <div className="flex flex-wrap gap-1">
                    {synonyms.map((syn) => (
                      <span
                        key={syn}
                        className="text-xs px-2 py-0.5 bg-vaultred/20 text-vaultred rounded-sm"
                      >
                        {syn}
                      </span>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <p className="text-copy text-sm">No matching fields.</p>
            )}
          </div>
          {relations && relations.length > 0 && (
            <div>
              <h3 className="text-md font-semibold text-vaultred mb-1">
                Relations
              </h3>
              <div className="overflow-x-auto border border-gray-700 rounded-md">
                <table className="min-w-full text-xs text-copy">
                  <thead>
                    <tr className="border-b border-gray-700">
                      <th className="px-2 py-1 text-left">From</th>
                      <th className="px-2 py-1 text-left">Relation</th>
                      <th className="px-2 py-1 text-left">To</th>
                    </tr>
                  </thead>
                  <tbody>
                    {relations.map((rel, idx) => (
                      <tr key={idx} className="border-b border-gray-800">
                        <td className="px-2 py-1 break-all">{rel.from}</td>
                        <td className="px-2 py-1 break-all text-neonCyan">
                          {rel.relation}
                        </td>
                        <td className="px-2 py-1 break-all">{rel.to}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}