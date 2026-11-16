import resources from '../../src/mocks/resources.json';
import { createPlanningMock } from '../../src/services/planning-mock';
import { initialMeta } from '../../src/services/resource-storage';
import type { ResourcesData } from '../../src/types/domain';
import type { PlanningInput } from '../../src/types/planning';
import { TestStorage } from './storage';
import { savedRequest, taskMeta } from './tasks';

function planningEnvironment(storage: TestStorage, initialTime: number) {
  let time = initialTime;
  const current = { resources: structuredClone(resources) as ResourcesData, meta: initialMeta() };
  const read = () => ({ ...current, meta: { ...current.meta, taskWorkspace: taskMeta(storage) } });
  const now = () => time;
  const advance = (ms = 10000) => { time += ms; };
  return { storage, current, read, now, advance, planning: createPlanningMock(() => storage, read, now) };
}

export async function prepareDiagnosisPlanningFixture(input: PlanningInput, options: {
  at: string;
  name: string;
  confirmed?: boolean;
}) {
  // 场景用例共享真实准备的规划输入。保存、转正和资源校验仍经过 Mock 服务，
  // 每个用例从独立存储、资源和时钟开始，避免重复准备规划挤占诊断用例的时间。
  const { storage, planning, now, advance } = planningEnvironment(new TestStorage(), Date.parse(options.at));
  const request = await savedRequest(storage, {
    baseRevision: 'initial', requestId: 'plan-submit', taskId: null,
    parentPlanVersionId: null, input: structuredClone(input),
  });
  const submitted = await planning.submitPlanning(request);
  advance();
  const saved = await planning.savePlanVersion({
    baseRevision: submitted.workspace.revision, requestId: 'plan-save', jobId: submitted.view.job.id,
    name: options.name, note: '',
  });
  const plan = options.confirmed === false ? saved.plan : (await planning.confirmPlanVersion({
    baseRevision: saved.workspace.revision, requestId: 'plan-confirm', planId: saved.plan.id,
  })).plan;
  const entries = [...storage.entries];
  const savedAt = now();
  return () => {
    const clone = new TestStorage();
    clone.entries = new Map(entries);
    return { ...planningEnvironment(clone, savedAt), plan: structuredClone(plan) };
  };
}
