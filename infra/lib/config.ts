export interface AgentStudioConfig {
  region: string;
  accountId: string;
  s3Bucket: string;
}

export function getConfig(): AgentStudioConfig {
  const required = (key: string): string => {
    const val = process.env[key];
    if (!val) throw new Error(`Missing env var: ${key}`);
    return val;
  };
  return {
    region: required("AGENT_STUDIO_REGION"),
    accountId: required("AGENT_STUDIO_ACCOUNT_ID"),
    s3Bucket: required("AGENT_STUDIO_S3_BUCKET"),
  };
}
