/* Mi cuenta del usuario aliado: contraseña (y su 2FA, con la misma API /auth/* del resto). Mismo patrón que la
   cuenta del portal del cliente. */
import ClientAccount from "../cliente/ClientAccount";

export default function AllyAccount() {
  return <ClientAccount />;
}
