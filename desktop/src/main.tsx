import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import InteractionController from './InteractionController';
import './styles.css';
import './writer.css';
import './product.css';
import './product-polish.css';
import './product-responsive.css';
import './interaction.css';
import './advanced-interaction.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
    <InteractionController />
  </StrictMode>,
);
