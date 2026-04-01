import { useEffect, useState } from "react";
import { Plug, RefreshCw, Loader2, ExternalLink, Globe } from "lucide-react";
import { fetchAuthSession } from "aws-amplify/auth";
import { agentConfig } from "../../config";

interface McpGateway {
  id: string;
  name: string;
  status: string;
}

interface McpTarget {
  name: string;
  description: string;
  endpointUrl: string;
  status: string;
}

async function fetchGateways(): Promise<McpGateway[]> {
  try {
    const { credentials } = await fetchAuthSession();
    if (!credentials) return [];

    const { SignatureV4 } = await import("@smithy/signature-v4");
    const { Sha256 } = await import("@aws-crypto/sha256-js");

    const signer = new SignatureV4({
      service: "bedrock-agentcore",
      region: agentConfig.region,
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
        sessionToken: credentials.sessionToken,
      },
      sha256: Sha256,
    });

    const url = new URL(`https://bedrock-agentcore-control.${agentConfig.region}.amazonaws.com/gateways/`);
    const signed = await signer.sign({
      method: "POST",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: {},
      headers: { "Content-Type": "application/json", Host: url.host },
      body: "",
    });

    const resp = await fetch(url.toString(), {
      method: "POST",
      headers: signed.headers as Record<string, string>,
      body: "",
    });

    if (!resp.ok) return [];
    const data = await resp.json();
    return (data.gateways || []).map((g: Record<string, string>) => ({
      id: g.gatewayId,
      name: g.name || g.gatewayId,
      status: g.status || "UNKNOWN",
    }));
  } catch {
    return [];
  }
}

async function fetchTargets(gatewayId: string): Promise<McpTarget[]> {
  try {
    const { credentials } = await fetchAuthSession();
    if (!credentials) return [];

    const { SignatureV4 } = await import("@smithy/signature-v4");
    const { Sha256 } = await import("@aws-crypto/sha256-js");

    const signer = new SignatureV4({
      service: "bedrock-agentcore",
      region: agentConfig.region,
      credentials: {
        accessKeyId: credentials.accessKeyId,
        secretAccessKey: credentials.secretAccessKey,
        sessionToken: credentials.sessionToken,
      },
      sha256: Sha256,
    });

    const url = new URL(`https://bedrock-agentcore-control.${agentConfig.region}.amazonaws.com/gateways/${gatewayId}/targets/`);
    const signed = await signer.sign({
      method: "POST",
      protocol: url.protocol,
      hostname: url.hostname,
      path: url.pathname,
      query: {},
      headers: { "Content-Type": "application/json", Host: url.host },
      body: "",
    });

    const resp = await fetch(url.toString(), {
      method: "POST",
      headers: signed.headers as Record<string, string>,
      body: "",
    });

    if (!resp.ok) return [];
    const data = await resp.json();
    return (data.targets || []).map((t: Record<string, string>) => ({
      name: t.name || t.targetId,
      description: t.description || "",
      endpointUrl: t.endpointUrl || "",
      status: t.status || "UNKNOWN",
    }));
  } catch {
    return [];
  }
}

export default function McpPage() {
  const [gateways, setGateways] = useState<McpGateway[]>([]);
  const [targets, setTargets] = useState<Record<string, McpTarget[]>>({});
  const [loading, setLoading] = useState(true);
  const [expandedGw, setExpandedGw] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    const gws = await fetchGateways();
    setGateways(gws);
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const toggleGateway = async (gwId: string) => {
    if (expandedGw === gwId) {
      setExpandedGw(null);
      return;
    }
    setExpandedGw(gwId);
    if (!targets[gwId]) {
      const t = await fetchTargets(gwId);
      setTargets((prev) => ({ ...prev, [gwId]: t }));
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
        <div>
          <h2 className="text-base font-semibold text-gray-900">MCP Servers</h2>
          <p className="text-xs text-gray-500">AgentCore Gateway MCP services available for your agents</p>
        </div>
        <button
          onClick={load}
          className="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
          </div>
        ) : gateways.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400">
            <Plug className="w-12 h-12 mb-3 opacity-30" />
            <p className="text-sm font-medium text-gray-600">No MCP Gateways found</p>
            <p className="text-xs mt-1">Create a Gateway in the AgentCore console to connect external tools</p>
          </div>
        ) : (
          <div className="space-y-3">
            {gateways.map((gw) => (
              <div key={gw.id} className="border border-gray-200 rounded-lg overflow-hidden">
                <button
                  onClick={() => toggleGateway(gw.id)}
                  className="w-full flex items-center justify-between px-4 py-3 hover:bg-gray-50 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <Globe className="w-4 h-4 text-purple-500" />
                    <div className="text-left">
                      <p className="text-sm font-medium text-gray-900">{gw.name}</p>
                      <p className="text-[10px] text-gray-400">{gw.id}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${gw.status === "ACTIVE" ? "bg-green-500" : "bg-yellow-500"}`} />
                    <span className="text-[10px] text-gray-400">{gw.status}</span>
                    <svg className={`w-4 h-4 text-gray-400 transition-transform ${expandedGw === gw.id ? "rotate-180" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </div>
                </button>

                {expandedGw === gw.id && (
                  <div className="border-t border-gray-100 px-4 py-3 bg-gray-50/50 space-y-2">
                    {!targets[gw.id] ? (
                      <div className="flex items-center gap-2 text-xs text-gray-400">
                        <Loader2 className="w-3 h-3 animate-spin" /> Loading targets...
                      </div>
                    ) : targets[gw.id].length === 0 ? (
                      <p className="text-xs text-gray-400">No targets configured</p>
                    ) : (
                      targets[gw.id].map((t) => (
                        <div key={t.name} className="flex items-center justify-between py-1.5 px-3 bg-white rounded-md border border-gray-100">
                          <div>
                            <p className="text-xs font-medium text-gray-800">{t.name}</p>
                            {t.description && <p className="text-[10px] text-gray-400">{t.description}</p>}
                          </div>
                          <div className="flex items-center gap-2">
                            <span className={`w-1.5 h-1.5 rounded-full ${t.status === "ACTIVE" ? "bg-green-500" : "bg-gray-300"}`} />
                          </div>
                        </div>
                      ))
                    )}
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
