import React from 'react';
import { useLocation } from 'react-router-dom';
import { motion } from 'framer-motion';

import collectiveIcon from '../../assets/collective.svg';
import memoryIcon from '../../assets/memory.svg';
import originsIcon from '../../assets/origins.svg';

const icons = [
  { path: '/collective', label: 'Collective Mind', icon: collectiveIcon },
  { path: '/memory', label: 'Memory Core', icon: memoryIcon },
  { path: '/origins', label: 'Origin Sequence', icon: originsIcon },
];

export const Toolbox: React.FC = () => {
  const location = useLocation();

  return (
    <div className="absolute top-4 left-4 flex flex-col gap-6 z-50">
      {icons.map(({ path, label, icon }) => {
        const isActive = location.pathname.startsWith(path);
        return (
          <motion.div
            key={path}
            whileHover={{ scale: 0.6 }}
            initial={false}
            animate={{ scale: isActive ? 0.6 : 0.6 }}
            transition={{ type: 'spring', stiffness: 300 }}
            className="relative group cursor-pointer"
          >
            <img src={icon} alt={label} className="w-12 h-12" />
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              whileHover={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3 }}
              className="absolute left-full ml-2 top-1/2 -translate-y-1/2 bg-white text-black text-xs px-2 py-1 rounded shadow-md whitespace-nowrap pointer-events-none"
            >
              {label}
            </motion.div>
          </motion.div>
        );
      })}
    </div>
  );
};
