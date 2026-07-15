import threading
import time

from Ammeters.Circutor_Ammeter import CircutorAmmeter
from Ammeters.Entes_Ammeter import EntesAmmeter
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter

threading.Thread(target=lambda: GreenleeAmmeter(5001).start_server(), daemon=True).start()
threading.Thread(target=lambda: EntesAmmeter(5002).start_server(), daemon=True).start()
threading.Thread(target=lambda: CircutorAmmeter(5003).start_server(), daemon=True).start()
time.sleep(1)

from src.testing.test_framework import AmmeterTestFramework

framework = AmmeterTestFramework()

print("=== single run_test('greenlee') ===")
result = framework.run_test("greenlee")
print("successful:", result["successful_measurements"], "/", result["requested_measurements"])
print("stats:", result["statistics"])

print("\n=== run_all_tests() ===")
combined = framework.run_all_tests()
print("comparison:", combined["comparison"])