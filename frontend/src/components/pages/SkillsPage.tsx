import { useEffect, useState } from "react";
import { Package, Plus, Search, RefreshCw, Loader2, ExternalLink } from "lucide-react";
import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../../config";

interface SkillInfo {
  id: string;
  name: string;
  description: string;
  author: string;
  version: string;
  tags: string[];
}

async function fetchSkills(): Promise<SkillInfo[]> {
  // Read skills from S3 skills/ prefix
  try {
    const { credentials } = await fetchAuthSession();
    if (!credentials) return [];

    const { SignatureV4 } = await import("@smithy/signature-v4");
    const { Sha256 } = await import("@aws-crypto/sha256-js");

    const signer = new SignatureV4({
      service: "s3",
      region: agentConfig.region,
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
        sessionToken: credentials.sessionToken,
      },
      sha256: Sha256,
    });

    const url = new URL(`https://s3.${agentConfig.region}.amazonaws.com/${agentConfig.s3Bucket}?list-type=2&prefix=skills/&delimiter=/`);
    const signed = await signer.sign({
      method: "GET",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: Object.fromEntries(url.searchParams),
      headers: { Host: url.host },
    });

    const resp = await fetch(url.toString(), {
      headers: signed.headers as Record<string, string>,
    });

    if (!resp.ok) return [];
    const xml = await resp.text();

    // Parse S3 list response for prefixes
    const prefixes = [...xml.matchAll(/<Prefix>(skills\/[^<]+\/)<\/Prefix>/g)].map(m => m[1]);
    // For now return stub data from prefix names
    return prefixes.map(p => {
      const id = p.replace("skills/", "").replace("/", "");
      return {
        id,
        name: id,
        description: "",
        author: "",
        version: "1.0",
        tags: [],
      };
    });
  } catch {
    return [];
  }
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  useEffect(() => {
    fetchSkills().then((s) => { setSkills(s); setLoading(false); });
  }, []);

  const filtered = skills.filter(s =>
    !search || s.name.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
        <div>
          <h2 className="text-base font-semibold text-gray-900">Skills</h2>
          <p className="text-xs text-gray-500">Reusable capabilities for your agents</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search skills..."
              className="pl-7 pr-3 py-1.5 text-xs border border-gray-200 rounded-lg w-48 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 outline-none"
            />
          </div>
          <button
            onClick={() => { setLoading(true); fetchSkills().then((s) => { setSkills(s); setLoading(false); }); }}
            className="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400">
            <Package className="w-12 h-12 mb-3 opacity-30" />
            <p className="text-sm font-medium text-gray-600">
              {search ? "No matching skills" : "No skills yet"}
            </p>
            <p className="text-xs mt-1">Create skills through the Meta Agent or import SKILL.md files</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((skill) => (
              <div key={skill.id} className="border border-gray-200 rounded-lg p-4 hover:border-blue-300 hover:shadow-sm transition-all">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    <Package className="w-4 h-4 text-blue-500 flex-shrink-0" />
                    <h3 className="text-sm font-medium text-gray-900">{skill.name}</h3>
                  </div>
                  <span className="text-[10px] text-gray-400">v{skill.version}</span>
                </div>
                {skill.description && (
                  <p className="text-xs text-gray-500 mt-2 line-clamp-2">{skill.description}</p>
                )}
                {skill.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {skill.tags.map(t => (
                      <span key={t} className="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded">{t}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
