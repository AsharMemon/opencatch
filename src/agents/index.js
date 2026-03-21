/**
 * Vast Agents - AI-powered fishing agents for OpenCatch
 *
 * Each agent is a specialized module that processes input and returns
 * structured recommendations. Agents can be composed together for
 * complex fishing intelligence.
 */

import { fishIdentificationAgent } from './fishIdentificationAgent';
import { weatherAgent } from './weatherAgent';
import { locationAgent } from './locationAgent';
import { catchLogAgent } from './catchLogAgent';

// Agent registry
const agents = {
  fishId: fishIdentificationAgent,
  weather: weatherAgent,
  location: locationAgent,
  catchLog: catchLogAgent,
};

/**
 * Run a single agent by name
 */
export async function runAgent(name, input) {
  const agent = agents[name];
  if (!agent) throw new Error(`Unknown agent: ${name}`);
  return agent.run(input);
}

/**
 * Run multiple agents in parallel and merge results
 */
export async function runAgents(agentInputs) {
  const results = await Promise.all(
    Object.entries(agentInputs).map(async ([name, input]) => {
      const result = await runAgent(name, input);
      return [name, result];
    })
  );
  return Object.fromEntries(results);
}

export { fishIdentificationAgent, weatherAgent, locationAgent, catchLogAgent };
