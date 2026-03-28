import * as vscode from 'vscode';
import type { BernsteinClient } from './BernsteinClient';
import type { AgentItem } from './AgentTreeProvider';
import type { OutputManager } from './OutputManager';
import { DashboardProvider } from './DashboardProvider';

export function registerCommands(
  context: vscode.ExtensionContext,
  client: BernsteinClient,
  outputManager: OutputManager,
  onRefresh: () => void,
): void {
  context.subscriptions.push(

    vscode.commands.registerCommand('bernstein.refresh', onRefresh),

    vscode.commands.registerCommand('bernstein.showDashboard', () => {
      DashboardProvider.openInBrowser(client.baseUrl);
    }),

    vscode.commands.registerCommand(
      'bernstein.killAgent',
      async (item: AgentItem) => {
        const answer = await vscode.window.showWarningMessage(
          `Kill agent ${item.agent.id}?`,
          { modal: true },
          'Kill',
        );
        if (answer === 'Kill') {
          try {
            await client.killAgent(item.agent.id);
            vscode.window.showInformationMessage(
              `Kill signal sent to ${item.agent.id}`,
            );
            onRefresh();
          } catch (e) {
            vscode.window.showErrorMessage(`Failed to kill agent: ${String(e)}`);
          }
        }
      },
    ),

    vscode.commands.registerCommand(
      'bernstein.showAgentOutput',
      (item: AgentItem) => {
        outputManager.show(item.agent.id);
      },
    ),

  );
}
