import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { IConstruct } from "constructs";

/**
 * Synth-time guard that ensures no IAM role can escape the
 * AgentStudioWorkspaceCeiling permission boundary model.
 *
 * Red lines (spec §9.3, P1-B from security review):
 *
 * 1. No role grants `iam:PutRolePermissionsBoundary` or
 *    `iam:DeleteRolePermissionsBoundary` on any resource — these would allow
 *    removing the boundary from a workspace role.
 * 2. No role grants `iam:CreatePolicyVersion` or `iam:SetDefaultPolicyVersion`
 *    on the ceiling ARN — these would mutate the ceiling itself.
 * 3. CRUD Lambda role does not grant `bedrock-agentcore:InvokeAgentRuntime`
 *    on any `runtime/asmcp_*` resource. CRUD only controls (create/delete)
 *    runtimes; it never invokes them. This removes CRUD as a cross-workspace
 *    invocation oracle if spec §4.2 Layer 1 (resource policy) is defeated.
 *
 * Violations fail synth with a clear error.
 */
export class EnforceBoundaryImmutability implements cdk.IAspect {
  private readonly ceilingArn: string;
  private readonly region: string;
  private readonly accountId: string;

  constructor(props: { ceilingArn: string; region: string; accountId: string }) {
    this.ceilingArn = props.ceilingArn;
    this.region = props.region;
    this.accountId = props.accountId;
  }

  public visit(node: IConstruct): void {
    if (!(node instanceof iam.CfnPolicy || node instanceof iam.CfnManagedPolicy)) {
      // PolicyStatement adds live on cfnPolicies attached to roles; we
      // only see the synthesized Cfn nodes.
      return;
    }

    const doc = (node as any).policyDocument;
    if (!doc) return;

    // CDK policy documents are PolicyDocument objects; resolve to JSON.
    const resolved = cdk.Stack.of(node).resolve(doc);
    const statements = resolved?.Statement || [];
    const stmts: any[] = Array.isArray(statements) ? statements : [statements];

    for (const s of stmts) {
      if (!s || s.Effect !== "Allow") continue;

      const actions = this._asArray(s.Action);
      const resources = this._asArray(s.Resource);

      // Rule 1: no PermissionsBoundary mutation.
      const boundaryMutators = [
        "iam:PutRolePermissionsBoundary",
        "iam:DeleteRolePermissionsBoundary",
      ];
      for (const banned of boundaryMutators) {
        if (this._matchesAny(banned, actions)) {
          cdk.Annotations.of(node).addError(
            `[EnforceBoundaryImmutability] Policy allows ${banned} — this would ` +
              `let a role remove the AgentStudioWorkspaceCeiling boundary from a ` +
              `workspace role, defeating the isolation model.`
          );
        }
      }

      // Rule 2: no ceiling policy version mutation.
      const ceilingMutators = ["iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion"];
      for (const banned of ceilingMutators) {
        if (this._matchesAny(banned, actions)) {
          // Only flag if resources include the ceiling ARN or "*".
          const touchesCeiling = resources.some(
            (r: string) => r === "*" || r === this.ceilingArn,
          );
          if (touchesCeiling) {
            cdk.Annotations.of(node).addError(
              `[EnforceBoundaryImmutability] Policy allows ${banned} on the ` +
                `ceiling policy. This would allow mutating the boundary itself.`
            );
          }
        }
      }

      // Rule 3: CRUD Lambda role must not have InvokeAgentRuntime on asmcp_*.
      // Detected by any statement with this action on a matching resource.
      const actionIsInvoke =
        this._matchesAny("bedrock-agentcore:InvokeAgentRuntime", actions) ||
        this._matchesAny("bedrock-agentcore-control:InvokeAgentRuntime", actions);
      if (actionIsInvoke) {
        const touchesMcp = resources.some(
          (r: string) => r === "*" || r.includes("runtime/asmcp_"),
        );
        if (touchesMcp && this._isCrudLambda(node)) {
          cdk.Annotations.of(node).addError(
            `[EnforceBoundaryImmutability] CRUD Lambda role grants ` +
              `bedrock-agentcore:InvokeAgentRuntime on runtime/asmcp_*. ` +
              `CRUD must only control (create/delete) MCP runtimes, never ` +
              `invoke them. See spec §4.2 fallback path.`
          );
        }
      }
    }
  }

  private _asArray(v: any): string[] {
    if (!v) return [];
    return Array.isArray(v) ? v : [v];
  }

  private _matchesAny(target: string, patterns: string[]): boolean {
    const t = target.toLowerCase();
    for (const p of patterns) {
      const pl = String(p).toLowerCase();
      if (pl === t) return true;
      // Glob: escape regex metachars except '*'
      const regex = new RegExp(
        "^" + pl.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*") + "$",
      );
      if (regex.test(t)) return true;
    }
    return false;
  }

  /** Heuristic: the CRUD Lambda role's CfnPolicy is attached to role ARN containing "agent-studio-crud". */
  private _isCrudLambda(node: IConstruct): boolean {
    // Walk up to find the Function it's attached to; or look at role references.
    // CfnPolicy has `roles` array of role LogicalIds. Pragmatic check: construct path.
    const path = node.node.path.toLowerCase();
    return path.includes("crudhandler") || path.includes("crud-lambda");
  }
}
