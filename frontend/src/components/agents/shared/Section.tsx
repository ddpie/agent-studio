function Section({ title, icon, action, children }: { title: string; icon?: React.ReactNode; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm overflow-hidden">
      <div className="px-3 py-1.5 border-b border-gray-100 dark:border-gray-700 bg-gradient-to-r from-gray-50 dark:from-gray-800 to-white dark:to-gray-800 flex items-center gap-1.5">
        {icon && <span className="text-gray-400">{icon}</span>}
        <h3 className="text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">{title}</h3>
        {action && <span className="ml-auto">{action}</span>}
      </div>
      <div className="px-3 py-3 space-y-3">{children}</div>
    </div>
  );
}

export default Section;
